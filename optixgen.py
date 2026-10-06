"""Deterministic generator for FactoryTalk Optix 1.7.3 projects, written in Studio's YAML style.

Shared by the OvenLine2 fixture and by the experiments that prove its building blocks.

- Instances of module types (Trend, DataGrid, SQLiteStore, DataLogger, NavigationPanel...) are
  written the way Studio writes them: every member their type declares Mandatory, with its default
  value and attributes, and one `Class: Reference` per mandatory method. All of it is read from the
  installed `Modules/*/Module.xml`, so nothing about a module type is typed by hand here.
- Node Ids derive from names (uuid5), so two builds on the same machine give byte-identical trees.
- Files are UTF-8 without BOM with CRLF line endings, as Studio writes them; the .sln alone has a BOM.

Standard library only. MIT licence (see LICENSE).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

CRLF = "\r\n"
STUDIO_ROOT = Path(r"C:\Program Files\Rockwell Automation\FactoryTalk Optix")

# --- YAML emission in Studio's style ---------------------------------------------------------

KEY_ORDER = ["Class", "Name", "Id", "Type", "Supertype", "Target", "Direction", "DataType",
             "ReferenceType", "ModellingRule", "AccessLevel", "ValueRank", "ArrayDimensions", "Value"]
RAW_KEYS = {"Name", "Type", "Supertype", "DataType", "ModellingRule", "Class", "Target", "Direction",
            "ReferenceType", "Id", "AccessLevel", "ValueRank"}


def scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(str(value), ensure_ascii=False)


def emit(node: dict, indent: int, first_prefix: str = "") -> list[str]:
    """Lines of one node. `first_prefix` is "- " for a list item."""
    lines = []
    pad = " " * indent
    keys = [k for k in KEY_ORDER if k in node] + [k for k in node if k not in KEY_ORDER and k != "Children"]
    for i, key in enumerate(keys):
        value = node[key]
        txt = value if key in RAW_KEYS else scalar(value)
        lead = pad + (first_prefix if i == 0 else " " * len(first_prefix))
        lines.append(f"{lead}{key}: {txt}")
    children = node.get("Children") or []
    if children:
        inner = indent + len(first_prefix)
        lines.append(" " * inner + "Children:")
        for child in children:
            lines += emit(child, inner, "- ")
    return lines


def dump(node: dict) -> str:
    return CRLF.join(emit(node, 0)) + CRLF


def write(path: Path, content: str, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.replace("\r\n", "\n").replace("\n", CRLF).encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)


class Ids:
    """Deterministic node Ids: the same key always gives the same `g=<32 hex>`."""

    def __init__(self, namespace_url: str):
        self.namespace = uuid.uuid5(uuid.NAMESPACE_URL, namespace_url)

    def __call__(self, key: str) -> str:
        return "g=" + uuid.uuid5(self.namespace, key).hex

    def guid(self, key: str) -> uuid.UUID:
        return uuid.uuid5(self.namespace, key)


# --- module types, read from the installed Module.xml --------------------------------------

MEMBER_REFS = {"HasComponent", "HasProperty", "HasOrderedComponent"}
FLOAT_TYPES = {"Float", "Double", "Size"}
INT_TYPES = {"Byte", "SByte", "Int16", "Int32", "Int64", "UInt16", "UInt32", "UInt64"}


@dataclass
class Member:
    name: str
    kind: str                    # Variable, Object or Method
    rule: str | None             # Mandatory, Optional...
    type_def: str | None
    data_type: str | None
    number: str | None           # NodeId number in the module namespace
    value: str | None
    value_rank: str | None
    access: int | None
    ref_type: str
    children: list["Member"] = field(default_factory=list)


@dataclass
class ModuleType:
    name: str
    module: str
    supertype: str | None
    members: list[Member]


def _target_name(elem: ET.Element | None, index: dict) -> str | None:
    nid = None if elem is None else elem.find("NodeId")
    if nid is None:
        return None
    return nid.get("alias") or index.get((nid.get("namespaceUri", ""), nid.get("number", "")))


def _members(elem: ET.Element, index: dict) -> list[Member]:
    out = []
    refs = elem.find("References")
    for ref in [] if refs is None else refs.findall("Reference"):
        if ref.get("reverse") == "true":
            continue
        ref_type = _target_name(ref.find("ReferenceType"), index)
        target = ref.find("Target")
        if ref_type not in MEMBER_REFS or target is None:
            continue
        for kind in ("Variable", "Object", "Method"):
            child = target.find(kind)
            if child is None or child.find("BrowseName") is None:
                continue
            type_def = None
            crefs = child.find("References")
            for r in [] if crefs is None else crefs.findall("Reference"):
                if _target_name(r.find("ReferenceType"), index) == "HasTypeDefinition":
                    type_def = _target_name(r.find("Target"), index)
            nid = child.find("NodeId")
            access = child.findtext("AccessLevel")
            out.append(Member(
                name=child.find("BrowseName").get("name"), kind=kind, rule=target.get("modelingRule"),
                type_def=type_def, data_type=_target_name(child.find("DataType"), index),
                number=None if nid is None else nid.get("number"),
                value=child.findtext("Value"), value_rank=child.findtext("ValueRank"),
                access=int(access) if access and access.strip().isdigit() else None,
                ref_type=ref_type, children=_members(child, index)))
    return out


class Modules:
    """Types of every installed module (the newest version of each), keyed by browse name."""

    def __init__(self, studio: Path):
        self.types: dict[str, ModuleType] = {}
        self.ambiguous: set[str] = set()
        self.ns: dict[str, int] = {}          # module name -> namespace index in the project
        index: dict[tuple[str, str], str] = {}
        parsed = []
        for folder in sorted((Path(studio) / "Modules").iterdir()):
            if not folder.is_dir() or folder.name.endswith(".Net"):
                continue
            versions = sorted((d for d in folder.iterdir() if d.is_dir()), key=_version_key)
            xml = versions[-1] / "Module.xml" if versions else None
            if xml is None or not xml.is_file():
                continue
            root = ET.parse(xml).getroot()
            nodes = root.find("Nodes")
            elements = [] if nodes is None else [e for e in nodes if e.tag in ("ObjectType", "VariableType",
                                                                                "DataType")]
            for e in elements:
                bn, nid = e.find("BrowseName"), e.find("NodeId")
                if bn is not None and nid is not None:
                    index[(nid.get("namespaceUri", ""), nid.get("number", ""))] = bn.get("name")
            parsed.append(((root.findtext("Name") or folder.name).strip(), elements))
        for module, elements in parsed:
            for e in elements:
                if e.tag == "DataType" or e.find("BrowseName") is None:
                    continue
                supertype = None
                refs = e.find("References")
                for r in [] if refs is None else refs.findall("Reference"):
                    if r.get("reverse") == "true" and _target_name(r.find("ReferenceType"), index) == "HasSubtype":
                        supertype = _target_name(r.find("Target"), index)
                name = e.find("BrowseName").get("name")
                if name in self.types:
                    if self.types[name].module != module:
                        self.ambiguous.add(name)
                    continue
                self.types[name] = ModuleType(name, module, supertype, _members(e, index))

    def chain(self, name: str):
        if name in self.ambiguous:
            raise KeyError(f"type {name} is declared by two modules")
        seen = set()
        while name in self.types and name not in seen:
            seen.add(name)
            yield self.types[name]
            name = self.types[name].supertype

    def member(self, type_name: str, member: str) -> Member:
        for t in self.chain(type_name):
            for m in t.members:
                if m.name == member:
                    return m
        raise KeyError(f"{type_name} has no member {member}")

    def module_of(self, type_name: str) -> str:
        return self.types[type_name].module

    # -- writing members and instances --

    def member_node(self, m: Member, module: str) -> dict:
        if m.kind == "Method":
            return method_ref(self.ns[module], m.number)
        node = {"Name": m.name, "Type": m.type_def or ("BaseObjectType" if m.kind == "Object"
                                                       else "BaseDataVariableType")}
        if m.kind == "Variable" and m.data_type:
            node["DataType"] = m.data_type
        if m.ref_type == "HasProperty" and m.type_def not in (None, "PropertyType"):
            node["ReferenceType"] = "HasProperty"
        if m.access == 1:
            node["AccessLevel"] = "Read"
        if m.value_rank and m.value_rank not in ("Scalar", "-1"):
            node["ValueRank"] = m.value_rank
            node["ArrayDimensions"] = [0, 0] if m.value_rank == "OneOrMoreDimensions" else [0]
        value = default_value(m)
        # Studio leaves out a default equal to the data type's zero (0, false, empty, zero duration).
        if value is not None and value not in (0, False, "") and value != duration(0):
            node["Value"] = value
        kids = [self.member_node(c, module) for c in m.children
                if c.rule == "Mandatory" and c.kind != "Method"]
        # The member's own type adds its mandatory members too (an axis typed ValueAxis gets Position).
        if m.type_def in self.types and m.type_def not in self.ambiguous:
            written = {k.get("Name") for k in kids}
            kids += [k for k in self.mandatory(m.type_def) if k.get("Name") not in written
                     and k.get("Class") != "Reference"]
        if kids:
            node["Children"] = kids
        return node

    def mandatory(self, type_name: str) -> list[dict]:
        """What an instance of `type_name` writes: its mandatory members, nearest type first."""
        out, seen = [], set()
        for t in self.chain(type_name):
            for m in t.members:
                if m.rule == "Mandatory" and m.name not in seen:
                    seen.add(m.name)
                    out.append(self.member_node(m, t.module))
        return out

    def instance(self, type_name: str, name: str, values: dict | None = None, extra=(),
                 node_id: str | None = None) -> dict:
        """An instance with its mandatory members. `values` maps a member path ("XAxis/Window") to a
        value, or to a dict merged into that member (fields, or "Children" appended)."""
        kids = self.mandatory(type_name)
        for path, v in (values or {}).items():
            target = find(kids, path)
            if isinstance(v, dict) and "LocaleId" not in v:   # a LocalizedText is a value, not a merge
                more = v.get("Children") or []
                target.update({k: x for k, x in v.items() if k != "Children"})
                if more:
                    target["Children"] = (target.get("Children") or []) + list(more)
            else:
                target["Value"] = v
        node = {"Name": name}
        if node_id:
            node["Id"] = node_id
        node["Type"] = type_name
        node["Children"] = kids + list(extra)
        return node

    def member_instance(self, type_name: str, name: str, value=None) -> dict:
        """An optional structured member (a Trend's Model pointer...) written with its mandatory
        children, as Studio writes it once the member is used."""
        m = self.member(type_name, name)
        node = self.member_node(m, self.module_of(type_name))
        if value is not None:
            node["Value"] = value
        return node

    def prop(self, type_name: str, name: str, value=None, linked: str | None = None,
             mode: int | None = None) -> dict:
        """An optional property of a module type, with a value or a dynamic link."""
        m = self.member(type_name, name)
        node = {"Name": name, "Type": m.type_def or "BaseDataVariableType"}
        if m.data_type:
            node["DataType"] = m.data_type
        if m.rule == "Optional" and linked is None:
            node["ModellingRule"] = "Optional"   # Studio drops Optional once a property carries a link
        if value is not None:
            node["Value"] = value
        if linked is not None:
            node["Children"] = [link(linked, mode)]
        return node


def _version_key(d: Path):
    return [int(x) if x.isdigit() else 0 for x in d.name.split(".")]


def default_value(m: Member):
    if m.value is None or not m.value.strip() or m.value.strip().startswith("<"):
        return None
    v = m.value.strip()
    if m.data_type == "Boolean":
        return v.lower() in ("1", "true")
    if m.data_type in FLOAT_TYPES:
        return float(v)
    if m.data_type == "Duration":
        return duration(float(v))
    if m.data_type in ("String", "Color"):
        return v
    try:
        return int(v)          # integers and enumerations
    except ValueError:
        return None


def duration(ms: float) -> str:
    """Studio's text form of a Duration: d:hh:mm:ss.fffffff."""
    ticks = round(ms * 10_000)
    seconds, frac = divmod(ticks, 10_000_000)
    minutes, s = divmod(seconds, 60)
    hours, mi = divmod(minutes, 60)
    days, h = divmod(hours, 24)
    return f"{days}:{h:02d}:{mi:02d}:{s:02d}.{frac:07d}"


def find(children: list[dict], path: str) -> dict:
    node = None
    level = children
    for part in path.split("/"):
        node = next((c for c in level if c.get("Name") == part), None)
        if node is None:
            raise KeyError(f"no member {path}")
        level = node.setdefault("Children", [])
    if not node.get("Children"):
        node.pop("Children", None)
    return node


# --- small node builders ---------------------------------------------------------------------

def method_ref(ns: int, number: str) -> dict:
    return {"Class": "Reference", "Target": f"ns={ns};i={number}", "Direction": "Forward"}


def link(path: str, mode: int | None = None) -> dict:
    node = {"Name": "DynamicLink", "Type": "DynamicLink", "DataType": "NodePath", "Value": path}
    if mode is not None:
        node["Children"] = [{"Name": "Mode", "Type": "BaseVariableType", "DataType": "DynamicLinkMode",
                             "Value": mode}]
    return node


def text(value: str) -> dict:
    return {"LocaleId": "en-US", "Text": value}


def variable(name: str, data_type: str, value=None, node_id: str | None = None, linked: str | None = None,
             mode: int | None = None) -> dict:
    node = {"Name": name}
    if node_id:
        node["Id"] = node_id
    node.update({"Type": "BaseDataVariableType", "DataType": data_type})
    if value is not None:
        node["Value"] = value
    if linked is not None:
        node["Children"] = [link(linked, mode)]
    return node


def pointer(name: str, value: str | None, kind: str | None = None, linked: str | None = None,
            mode: int | None = None) -> dict:
    """A NodePointer member (Model, Store, Panel...) with its Kind."""
    node = {"Name": name, "Type": "NodePointer", "DataType": "NodeId"}
    if value is not None:
        node["Value"] = value
    kids = [{"Name": "Kind", "Type": "PropertyType", "DataType": "NodeId", **({"Value": kind} if kind else {})}]
    if linked is not None:
        kids.append(link(linked, mode))
    node["Children"] = kids
    return node


def folder(name: str, *children: dict) -> dict:
    return {"Name": name, "Type": "FolderType", "Children": list(children)}


def mandatory_copy(children: list[dict]) -> list[dict]:
    """What an instance of a project type writes: every member of the type declared without
    ModellingRule, recursively, with its value or link. Optional members are left out: the Runtime
    takes them from the type. A mandatory member that is not written does not exist at runtime."""
    out = []
    for child in children:
        if "Name" not in child or child.get("ModellingRule"):
            continue
        node = {k: v for k, v in child.items() if k not in ("Children", "Id")}
        inner = mandatory_copy(child.get("Children") or [])
        if inner:
            node["Children"] = inner
        out.append(node)
    return out


MOUSE_CLICK_ARGS = [("EventId", "ByteString"), ("EventType", "NodeId"), ("SourceNode", "NodeId"),
                    ("SourceName", "String"), ("Time", "UtcTime"), ("ReceiveTime", "UtcTime"),
                    ("Message", "LocalizedText"), ("Severity", "UInt16")]


def click_handler(corebase_ns: int, method: str, object_path: str | None = None,
                  object_link: str | None = None, arguments: list[dict] = (), name: str = "MouseClickEventHandler1") -> dict:
    """A button's click handler calling `method` on an object (absolute path, or relative link)."""
    obj = {"Name": f"ns={corebase_ns};ObjectPointer", "Type": "NodePointer", "DataType": "NodeId"}
    if object_path is not None:
        obj["Value"] = object_path
    kids = [{"Name": "Kind", "Type": "PropertyType", "DataType": "NodeId",
             "Value": "/Types/ObjectTypes/BaseObjectType"}]
    if object_link is not None:
        kids.append(link(object_link, 2))
    obj["Children"] = kids
    container = [obj, {"Name": f"ns={corebase_ns};Method", "Type": "BaseDataVariableType", "DataType": "String",
                       "Value": method}]
    if arguments:
        container.append({"Name": "InputArguments", "Type": "BaseObjectType", "Children": list(arguments)})
    return {"Name": name, "Type": "EventHandler", "Children": [
        {"Name": "ListenEventType", "Type": "PropertyType", "DataType": "NodeId",
         "Value": "/Types/EventTypes/BaseEventType/MouseEvent/MouseClickEvent"},
        {"Name": "MethodsToCall", "Type": "BaseObjectType", "Children": [
            {"Name": "MethodContainer1", "Type": "BaseObjectType", "Children": container}]},
        {"Name": "EventArguments", "Type": "MouseClickEvent", "Children": [
            {"Name": n, "Type": "PropertyType", "DataType": dt} for n, dt in MOUSE_CLICK_ARGS]},
    ]}


# --- installed Studio ------------------------------------------------------------------------

def find_studio() -> Path:
    candidates = sorted(STUDIO_ROOT.glob("Studio *"))
    if not candidates:
        raise SystemExit("FactoryTalk Optix Studio not found; pass --studio")
    return candidates[-1]


def installed_version(studio: Path, module: str) -> str:
    folders = sorted((d for d in (studio / "Modules" / module).iterdir() if d.is_dir()), key=_version_key)
    if not folders:
        raise SystemExit(f"module {module} not installed in {studio}")
    return folders[-1].name


def references_xml(studio: Path) -> str:
    """Assembly references for the NetSolution, as Studio writes them: every .Net module plus the core bindings."""
    entries = []
    modules = sorted(d.name for d in (studio / "Modules").iterdir()
                     if d.is_dir() and (d.name.endswith(".Net") or d.name in ("FTOptix.NetLogic",
                                                                              "FTOptix.EdgeAppPlatform")))
    for module in modules:
        dll = studio / "Modules" / module / installed_version(studio, module) / "Any" / f"{module}.dll"
        if dll.is_file():
            entries.append((module, dll))
    bindings = studio / "Bindings" / "Net"
    if bindings.is_dir():
        version = sorted(d.name for d in bindings.iterdir() if d.is_dir())[-1]
        for name in ("UAManagedCore", "UAManagedCoreCommon"):
            dll = bindings / version / "Win32_x64" / f"{name}.dll"
            if dll.is_file():
                entries.append((name, dll))
    lines = ["<Project>", "  <ItemGroup>"]
    for name, dll in entries:
        lines += [f'    <Reference Include="{name}">', f"      <HintPath>{dll.as_posix()}</HintPath>",
                  "      <Private>False</Private>", "    </Reference>"]
    lines += ["  </ItemGroup>", "</Project>"]
    return CRLF.join(lines) + CRLF


CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <AppendTargetFrameworkToOutputPath>false</AppendTargetFrameworkToOutputPath>
    <CopyLocalLockFileAssemblies>true</CopyLocalLockFileAssemblies>
  </PropertyGroup>
  <PropertyGroup Condition="'$(Configuration)|$(Platform)'=='Debug|AnyCPU'">
    <OutputPath>bin\\</OutputPath>
    <NoWarn>1701;1702</NoWarn>
  </PropertyGroup>
  <PropertyGroup Condition="'$(Configuration)|$(Platform)'=='Release|AnyCPU'">
    <OutputPath>bin\\</OutputPath>
    <NoWarn>1701;1702</NoWarn>
  </PropertyGroup>
  <PropertyGroup>
    <ResolveAssemblyWarnOrErrorOnTargetArchitectureMismatch>None</ResolveAssemblyWarnOrErrorOnTargetArchitectureMismatch>
  </PropertyGroup>
  <Import Project="{name}.references"/>
</Project>
"""

SLN = """Microsoft Visual Studio Solution File, Format Version 12.00
# Visual Studio 15
VisualStudioVersion = 15.0.28307.106
MinimumVisualStudioVersion = 10.0.40219.1
Project("{{9A19103F-16F7-4668-BE54-9A1E7A4F7556}}") = "{name}", "{name}.csproj", "{{{guid}}}"
EndProject
Global
    GlobalSection(SolutionConfigurationPlatforms) = preSolution
        Debug|Any CPU = Debug|Any CPU
        Release|Any CPU = Release|Any CPU
    EndGlobalSection
    GlobalSection(ProjectConfigurationPlatforms) = postSolution
        {{{guid}}}.Debug|Any CPU.ActiveCfg = Debug|Any CPU
        {{{guid}}}.Debug|Any CPU.Build.0 = Debug|Any CPU
        {{{guid}}}.Release|Any CPU.ActiveCfg = Release|Any CPU
        {{{guid}}}.Release|Any CPU.Build.0 = Release|Any CPU
    EndGlobalSection
    GlobalSection(SolutionProperties) = preSolution
        HideSolutionNode = FALSE
    EndGlobalSection
EndGlobal
"""


def write_solution(project: Path, name: str, sources: dict[str, str], studio: Path, ids: Ids) -> None:
    solution = project / "ProjectFiles" / "NetSolution"
    for file_name, code in sources.items():
        write(solution / file_name, code)
    write(solution / f"{name}.csproj", CSPROJ.format(name=name))
    write(solution / f"{name}.references", references_xml(studio))
    write(solution / f"{name}.sln", SLN.format(name=name, guid=str(ids.guid("netsolution")).upper()), bom=True)


# --- skeleton from the official CLI, then assembly --------------------------------------------

def cli_new(studio: Path, name: str, target: Path, window: tuple[int, int]) -> Path:
    """Run `FTOptixStudio.com new`, minimised without activation, then close the IDE it leaves open."""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    target.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([str(studio / "FTOptixStudio.com"), "new", name, str(target),
                             f"--main-window-width={window[0]}", f"--main-window-height={window[1]}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, startupinfo=si)
    created = threading.Event()

    def read() -> None:
        for raw in proc.stdout:
            if b"Created new project" in raw:
                created.set()

    threading.Thread(target=read, daemon=True).start()
    if created.wait(300):
        time.sleep(1.0)
        subprocess.run(["taskkill", "/PID", str(proc.pid)], capture_output=True)  # WM_CLOSE, not /F
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"], capture_output=True)
    skeleton = target / name
    if not (skeleton / f"{name}.optix").is_file():
        raise SystemExit("`FTOptixStudio.com new` did not create the project")
    return skeleton


def normalise_ids(folder: Path, ids: Ids) -> None:
    """Replace the random Ids created by `new` with ones derived from file and position."""
    for f in sorted(folder.rglob("*.yaml")):
        rel = f.relative_to(folder).as_posix()
        count = iter(range(100_000))
        content = f.read_bytes().decode("utf-8")
        content = re.sub(r"Id: g=[0-9a-f]{32}", lambda m: f"Id: {ids(f'skeleton:{rel}:{next(count)}')}", content)
        f.write_bytes(content.encode("utf-8"))


def set_dependencies(optix: Path, studio: Path, modules: list[str], ids: Ids) -> dict[str, int]:
    """Keep the dependencies written by `new`, append `modules` at the next free indices, derive the
    project GUID from `ids`. Returns module name -> namespace index (the project's own included)."""
    content = optix.read_bytes().decode("utf-8")
    head, rest = content.split(" Dependencies:", 1)
    deps, tail = rest.split(" Statistics:", 1)
    indices: dict[str, int] = {}
    for m in re.finditer(r"^  (\d+):\r?\n   Uri: [^\r\n]*\r?\n   Module: (\S+)", deps, flags=re.M):
        indices[m.group(2)] = int(m.group(1))
    project_ns = int(re.search(r"ProjectNamespaceIndex: (\d+)", head).group(1))
    lines = [line.rstrip("\r") for line in deps.split("\n") if line.startswith("  ")]
    nxt = max([*indices.values(), project_ns]) + 1
    for module in modules:
        if module in indices:
            continue
        version = ".".join(installed_version(studio, module).split(".")[:2])
        lines += [f"  {nxt}:", f"   Uri: 'urn:{module.replace('.', ':')}'", f"   Module: {module}",
                  f"   Version: {version}"]
        indices[module] = nxt
        nxt += 1
    head = re.sub(r"GUID: [0-9a-f]{32}", "GUID: " + ids.guid("project").hex, head)
    write(optix, head + " Dependencies:" + CRLF + CRLF.join(lines) + CRLF + " Statistics:" + tail)
    return {**indices, "project": project_ns}


def add_children(yaml_file: Path, nodes: list[dict]) -> None:
    """Append top-level children to a category file (Model, DataStores, Loggers, NetLogic...),
    before its `- File:` entries, the way Studio orders them."""
    lines = yaml_file.read_bytes().decode("utf-8").rstrip(CRLF).split(CRLF)
    if "Children:" not in lines:
        lines.append("Children:")
    at = next((i for i, x in enumerate(lines) if x.startswith("- File:")), len(lines))
    new = [x for n in nodes for x in emit(n, 0, "- ")]
    write(yaml_file, CRLF.join(lines[:at] + new + lines[at:]) + CRLF)


def add_to_main_window(ui_yaml: Path, window_children: list[dict], ui_nodes: list[dict]) -> None:
    """Append children to MainWindow (the last node `new` writes before the `- File:` entries) and
    new top-level UI nodes after it."""
    lines = ui_yaml.read_bytes().decode("utf-8").rstrip(CRLF).split(CRLF)
    at = next(i for i, x in enumerate(lines) if x.startswith("- File:"))
    start = max(i for i, x in enumerate(lines[:at]) if x.startswith("- Name: "))
    if lines[start] != "- Name: MainWindow":
        raise SystemExit("MainWindow is not the last UI node before the file entries")
    inside = [x for n in window_children for x in emit(n, 2, "- ")]
    outside = [x for n in ui_nodes for x in emit(n, 0, "- ")]
    write(ui_yaml, CRLF.join(lines[:at] + inside + outside + lines[at:]) + CRLF)


def copy_skeleton(skeleton: Path, work: Path, ids: Ids) -> None:
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(skeleton, work, ignore=shutil.ignore_patterns("*.optix.design"))
    normalise_ids(work, ids)
