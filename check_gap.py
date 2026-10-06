"""Check that OvenLine2 still has the gap it was built with: zone 5 is commissioned but not shown.

The model has five oven zones and the simulation drives all of them, but the HMI shows zones 1 to 4.
A zone is shown in seven places; `footprint()` lists them for any zone:

    tile            the ZoneTile instance on OVERVIEW whose alias points at the zone
    live_pen        the live trend's pen linked to the zone's logged PV
    logger_vars     the data logger's VariablesToLog linked to the zone's PV and SP
    history_pens    the run curve's pens named after the zone's logged columns
    store_columns   the store columns for the zone (run average, logged PV and SP)
    grid_column     the bake history grid column reading the zone's run average
    journal         the zone's entry in OvenZones.Shown, the one zone list in the C# code

The untouched project must have all seven places for zones 1 to 4, none for zone 5, room for a fifth
tile, a free trend colour and a fifth grid column, and no communication driver or database file.

Usage: python check_gap.py [project_folder]   (exit 0 = gap intact, 1 = gap closed or broken)
Standard library only: a small reader for the subset of YAML that Optix writes.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

KEY = re.compile(r"^( *)(- )?([\w;=]+): ?(.*)$")
ZONES = ["Z1", "Z2", "Z3", "Z4", "Z5"]
SHOWN = ["Z1", "Z2", "Z3", "Z4"]
PLACES = ["tile", "live_pen", "logger_vars", "history_pens", "store_columns", "grid_column", "journal"]
TILE_STEP, TILE_WIDTH = 248.0, 232.0


@dataclass
class Node:
    name: str
    path: str
    fields: dict = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None

    @property
    def type(self) -> str | None:
        return self.fields.get("Type")

    def child(self, name: str) -> "Node | None":
        return next((c for c in self.children if c.name == name), None)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def link(self) -> str | None:
        """The value of this node's DynamicLink, if it has one."""
        dl = self.child("DynamicLink")
        return dl.fields.get("Value") if dl else None

    def number(self, prop: str) -> float | None:
        c = self.child(prop)
        try:
            return float(c.fields["Value"]) if c and "Value" in c.fields else None
        except (TypeError, ValueError):
            return None


def _value(raw: str):
    raw = raw.strip()
    if raw.startswith(("\"", "{", "[")):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def read_tree(path: Path, parent: Node) -> None:
    """Read one YAML file of the project: its root node becomes a child of `parent`. `- File:` entries
    include another file whose root becomes a child of the node that lists them."""
    stack: list[tuple[int, Node]] = []
    current: Node | None = None
    for line in path.read_bytes().decode("utf-8-sig").splitlines():
        m = KEY.match(line)
        if not m:
            continue
        indent, dash, key, value = len(m.group(1)), m.group(2), m.group(3), m.group(4)
        if dash:
            level = indent + 2
            while stack and stack[-1][0] >= level:
                stack.pop()
        if dash and key == "File":
            read_tree(path.parent / value.strip().strip("'\""), stack[-1][1])
            current = None
        elif key == "Name" and not dash and not stack:
            current = Node(value.strip(), f"{parent.path}/{value.strip()}", parent=parent)
            parent.children.append(current)
            stack = [(0, current)]
        elif key == "Name" and dash:
            owner = stack[-1][1]
            current = Node(value.strip(), f"{owner.path}/{value.strip()}", parent=owner)
            owner.children.append(current)
            stack.append((level, current))
        elif dash and key == "Class":
            current = None          # a reference entry, not a node
        elif current is not None and key != "Children":
            current.fields[key] = _value(value)


def load(project: Path) -> Node:
    optix = next(project.glob("*.optix"))
    objects = Node("Objects", "/Objects")
    read_tree(project / "Nodes" / f"{optix.stem}.yaml", objects)
    return objects.children[0]


def find(root: Node, path: str) -> Node | None:
    node = root
    for part in path.strip("/").split("/"):
        node = node.child(part) if node else None
    return node


def footprint(project: Path, zone: str, root: Node | None = None) -> dict[str, list[str]]:
    """The places where the HMI shows `zone`, as lists of node paths (or file names for the C#)."""
    root = root or load(project)
    ui = [n for top in ("UI",) if find(root, top) for n in find(root, top).walk()]
    data = [n for top in ("Loggers", "DataStores") if find(root, top) for n in find(root, top).walk()]
    out: dict[str, list[str]] = {p: [] for p in PLACES}
    for n in ui:
        alias = n.child("Zone")
        if n.type == "ZoneTile" and alias and str(alias.fields.get("Value", "")).endswith(f"/Zones/{zone}"):
            out["tile"].append(n.path)
        if n.type == "TrendPen":
            link = n.link()
            if link and (f"{zone}_PV" in link or f"/Zones/{zone}/" in link):
                out["live_pen"].append(n.path)
            elif not link and n.name.startswith(f"{zone}_"):
                out["history_pens"].append(n.path)
        if n.type == "DataGridColumn":
            template = n.child("DataItemTemplate")
            text = template.child("Text") if template else None
            if text and str(text.link() or "").startswith(f"{{Item}}/{zone}_"):
                out["grid_column"].append(n.path)
    for n in data:
        if n.type == "VariableToLog" and re.search(rf"Zones/{zone}/(PV|SP)$", str(n.link() or "")):
            out["logger_vars"].append(n.path)
        if n.type == "StoreColumn" and n.name.startswith(f"{zone}_"):
            out["store_columns"].append(n.path)
    zones_cs = project / "ProjectFiles" / "NetSolution" / "OvenZones.cs"
    if zones_cs.is_file():
        shown = re.search(r"Shown\s*=\s*\{([^}]*)\}", zones_cs.read_text(encoding="utf-8"))
        if shown and f'"{zone}"' in shown.group(1):
            out["journal"].append("OvenZones.cs: Shown")
    return out


def _rect(n: Node, tile_width: float) -> tuple[float, float, float, float] | None:
    x, y = n.number("LeftMargin"), n.number("TopMargin")
    if x is None or y is None:
        return None
    w = n.number("Width") or (tile_width if n.type == "ZoneTile" else 0.0)
    h = n.number("Height") or 0.0
    return x, y, w, h


def checks(project: Path) -> list[tuple[str, bool, str]]:
    root = load(project)
    results: list[tuple[str, bool, str]] = []

    zones = find(root, "Model/Oven/Zones")
    members = {z: sorted(c.name for c in zones.child(z).children) if zones and zones.child(z) else None for z in ZONES}
    same = all(m == ["Output", "PV", "SP"] for m in members.values())
    results.append(("zones 1 to 5 in the model, each with PV, SP and Output", same, str(members)))
    sim = project / "ProjectFiles" / "NetSolution" / "OvenSimulation.cs"
    drives = sim.is_file() and re.search(r"ZoneCount\s*=\s*5\b", sim.read_text(encoding="utf-8")) is not None
    results.append(("the simulation drives all five zones", drives, sim.name))

    prints = {z: footprint(project, z, root) for z in ZONES}
    for z in SHOWN:
        missing = [p for p in PLACES if not prints[z][p]]
        results.append((f"{z} shown in all seven places", not missing, f"missing: {missing}" if missing else "ok"))
    found = {p: v for p, v in prints["Z5"].items() if v}
    results.append(("Z5 shown nowhere", not found, str(found) if found else "ok"))

    leaks = []
    for f in sorted((project / "Nodes").rglob("*.yaml")):
        if f.parent.name == "Model":
            continue
        if re.search(r"\bZ5\b|Zone 5", f.read_text(encoding="utf-8")):
            leaks.append(str(f.relative_to(project)))
    for f in sorted((project / "ProjectFiles" / "NetSolution").glob("*.cs")):
        if f.name != "OvenSimulation.cs" and re.search(r"\bZ5\b", f.read_text(encoding="utf-8")):
            leaks.append(f.name)
    results.append(("no Z5 outside the model", not leaks, str(leaks) if leaks else "ok"))
    csv = project / "ProjectFiles" / "NetSolution" / "CsvExport.cs"
    generic = csv.is_file() and not re.search(r"\bZ\d", csv.read_text(encoding="utf-8"))
    results.append(("CSV export names no zone (it exports every column)", generic, csv.name))

    tile_type = find(root, "UI/ZoneTile")
    tile_w = tile_type.number("Width") if tile_type else TILE_WIDTH
    overview = find(root, "UI/Screens/Overview")
    tiles = [n for n in overview.children if n.type == "ZoneTile"] if overview else []
    lefts = sorted(n.number("LeftMargin") or 0.0 for n in tiles)
    free = None
    if lefts:
        x = lefts[-1] + TILE_STEP
        y = tiles[0].number("TopMargin") or 0.0
        free = (x, y, tile_w, tile_type.number("Height") or 152.0)
        clash = []
        for n in overview.children:
            r = _rect(n, tile_w)
            if r and n not in tiles and r[0] < free[0] + free[2] and free[0] < r[0] + r[2] \
                    and r[1] < free[1] + free[3] and free[1] < r[1] + r[3]:
                clash.append(n.name)
        fits = free[0] + free[2] <= 1280 and not clash
        results.append(("room for a fifth tile", fits, f"slot {free}, overlaps {clash}" if clash else f"slot {free}"))

    used = set()
    for n in find(root, "UI").walk():
        link = str(n.link() or "")
        m = re.search(r"/Palette/(Series\d+)$", link)
        if m:
            used.add(m.group(1))
    series = [c.name for c in find(root, "UI/Palette").children if c.name.startswith("Series")]
    spare = [s for s in series if s not in used]
    results.append(("a free trend colour in the palette", bool(spare), f"used {sorted(used)}, free {spare}"))

    grid = next((n for n in find(root, "UI/Screens/BakeHistory").walk() if n.type == "DataGrid"), None)
    if grid is not None:
        widths = [c.number("Width") or 0.0 for c in grid.child("Columns").children]
        room = (grid.number("Width") or 0.0) - sum(widths)
        z_width = max((c.number("Width") or 0.0) for c in grid.child("Columns").children
                      if c.name.endswith("_AvgColumn"))
        results.append(("room for a fifth zone column in the grid", room >= z_width + 16,
                        f"{room:.0f} px free, a zone column takes {z_width:.0f} px + scroll bar"))

    drivers = find(root, "CommDrivers")
    results.append(("no communication driver", drivers is None or not drivers.children, "CommDrivers"))
    dbs = [str(p.relative_to(project)) for p in project.rglob("*.db")]
    results.append(("no database file", not dbs, str(dbs) if dbs else "ok"))
    return results


def main() -> int:
    project = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "OvenLine2"
    if not next(project.glob("*.optix"), None):
        print(f"no .optix file in {project}")
        return 1
    ok = True
    for label, passed, detail in checks(project):
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {label}  ({detail})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
