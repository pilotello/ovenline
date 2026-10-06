"""Build the OvenLine2 fixture: a public FactoryTalk Optix project for a fictional bakery's tunnel oven.

The project starts from what the official CLI creates (`FTOptixStudio.com new`); this script adds
the content with `optixgen`:

- Model: five oven zones (ZoneType: PV, SP, Output), the belt, the product, the current run, three
  alarm conditions, the weekly preheat schedule (DayType x 7, retained) and the HMI's own variables.
  A NetLogic simulation stands in for the PLC: no controller, no communication driver.
- Store and logger: a SQLite store with the bake history (BakeRuns) and the zone temperature log
  (OvenLogger); the logger records zones 1 to 4 every two seconds.
- UI, styled with the Exploded design system's tokens (no orange): header, tabs, footer, and four
  screens, OVERVIEW (zone tiles, live trend), SCHEDULE (seven day cards), BAKE HISTORY (filters, grid,
  run detail, run curve, CSV export) and ALARMS.

The deliberate gap: zone 5 is commissioned in the model and the simulation drives it, but the HMI
shows zones 1 to 4 only. check_gap.py proves it; the flagship request asks an agent to close it.

Every node Id derives from names, so two builds on the same machine give byte-identical trees.

Usage:
    python build_fixture.py [--out OvenLine2] [--studio DIR] [--skeleton DIR]

Standard library only. MIT licence (see LICENSE). Fonts: SIL Open Font License (see fonts/).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import optixgen as g  # noqa: E402

PROJECT = "OvenLine2"
ROOT = f"/Objects/{PROJECT}"
WINDOW = (1280, 800)
BAKERY = "Brindle & Rye Bakehouse"
IDS = g.Ids("https://github.com/pilotello/ovenline2")
MODULES = ["FTOptix.DataLogger", "FTOptix.Store", "FTOptix.SQLiteStore", "FTOptix.Alarm", "FTOptix.Core.Net",
           "FTOptix.CoreBase.Net", "FTOptix.UI.Net", "FTOptix.DataLogger.Net", "FTOptix.Store.Net",
           "FTOptix.Retentivity.Net", "FTOptix.HMIProject.Net", "FTOptix.NativeUI.Net", "FTOptix.SQLiteStore.Net",
           "FTOptix.Alarm.Net", "FTOptix.NetLogic"]
NETLOGIC = ["OvenSimulation", "RunJournal", "HistoryFilter", "CsvExport", "PreheatPlanner", "AlarmBadge",
            "HeaderClock"]
FONTS = ["BarlowCondensed-SemiBold.ttf", "Barlow-Regular.ttf", "Barlow-Medium.ttf", "FragmentMono-Regular.ttf"]

# --- design tokens (Exploded design system, docs/design/exploded-design-system.pdf; no orange) ----------
NIGHT, NIGHT_2, NIGHT_RULE = "#0F0F0D", "#1A1A17", "#3A3934"
ON_NIGHT, ON_NIGHT_2 = "#F2F0E9", "#ABA89E"
PAPER, SHEET, INK, MUTED, FAINT, GRID = "#F2F0E9", "#FAF9F5", "#161614", "#4F4D46", "#65625A", "#E3E0D6"
ADD, ERR = "#1E7B3C", "#B42318"
# Trend series, validated on SHEET (dataviz validator: lightness, chroma, CVD, contrast all pass).
SERIES = ["#2A78D6", "#13936A", "#4A3AA7", "#D0598A", "#6B7A1F", "#8C4A9E"]
CONDENSED, TEXT, MONO = "Barlow Condensed", "Barlow", "Fragment Mono"
PLAIN_PENS = ["#0000FF", "#FF0000", "#008000", "#FFA500", "#800080", "#000000"]
SEMIBOLD = 600

ZONES = ["Z1", "Z2", "Z3", "Z4", "Z5"]
SHOWN = ["Z1", "Z2", "Z3", "Z4"]               # the zones the HMI shows; Z5 is the gap
SETPOINTS = {"Z1": 425.0, "Z2": 450.0, "Z3": 460.0, "Z4": 450.0, "Z5": 430.0}
ALARMS = [("ExhaustAirflowLow", "Exhaust fan airflow low"), ("InletDoorOpen", "Oven inlet door open"),
          ("BeltSpeedDeviation", "Belt speed deviation")]
STRETCH = 3

# FTOptix.Alarm 6.1: NodeIds of the methods an alarm controller instance references.
ALARM_METHODS_FIRST = [81, 82, 83]
ALARM_METHODS_LAST = [130, 132, 135, 146, 148, 150, 152, 154]


def palette(name: str) -> str:
    return f"{ROOT}/UI/Palette/{name}"


def model(path: str) -> str:
    return f"{ROOT}/Model/{path}"


# --- model -----------------------------------------------------------------------------------------

def model_nodes() -> list[dict]:
    zone_type = {"Name": "ZoneType", "Id": IDS("Model/ZoneType"), "Supertype": "BaseObjectType", "Children": [
        g.variable("PV", "Float"), g.variable("SP", "Float"), g.variable("Output", "Float")]}
    day_type = {"Name": "DayType", "Id": IDS("Model/DayType"), "Supertype": "BaseObjectType", "Children": [
        g.variable("Enabled", "Boolean"), g.variable("StartMinutes", "Int32"), g.variable("DayName", "String"),
        g.variable("StartText", "String"), g.variable("ReadyText", "String"), g.variable("IsToday", "Boolean")]}
    zones = g.folder("Zones", *[{"Name": z, "Type": "ZoneType", "Children": [
        g.variable("PV", "Float", SETPOINTS[z]), g.variable("SP", "Float", SETPOINTS[z]),
        g.variable("Output", "Float")]} for z in ZONES])
    oven = g.folder(
        "Oven", zones,
        g.folder("Belt", g.variable("SpeedFtMin", "Float", 6.8), g.variable("BakeTimeMin", "Float", 14.0),
                 g.variable("Running", "Boolean", True)),
        g.folder("Product", g.variable("Name", "String", "Sandwich loaf 20 oz"), g.variable("Code", "String", "SL20")),
        g.folder("Run", g.variable("Number", "String", "2026-1049"), g.variable("StartText", "String", "12:44 PM"),
                 g.variable("State", "String", "BAKING"), g.variable("Units", "Int32", 520)),
        g.folder("Alarms", *[g.variable(name, "Boolean") for name, _ in ALARMS]))
    # Weekday mornings, Saturday later, Sunday off. Retained: these Ids must never change.
    starts = [210, 210, 210, 210, 225, 300, 360]
    days = [{"Name": f"Day{d}", "Id": IDS(f"Model/HMI/Schedule/Day{d}"), "Type": "DayType", "Children": [
        g.variable("Enabled", "Boolean", d != 7, IDS(f"Model/HMI/Schedule/Day{d}/Enabled")),
        g.variable("StartMinutes", "Int32", starts[d - 1], IDS(f"Model/HMI/Schedule/Day{d}/StartMinutes")),
        g.variable("DayName", "String"), g.variable("StartText", "String"), g.variable("ReadyText", "String"),
        g.variable("IsToday", "Boolean")]} for d in range(1, 8)]
    schedule = {"Name": "Schedule", "Id": IDS("Model/HMI/Schedule"), "Type": "FolderType", "Children": [
        *days, g.variable("WarmupMin", "Int32", 75), g.variable("TimeZoneText", "String"),
        g.variable("NextStartText", "String")]}
    history = g.folder(
        "History",
        g.variable("Search", "String", ""), g.variable("RangeDays", "Int32", 30), g.variable("Product", "String", ""),
        g.variable("GridQuery", "String", "SELECT rowid AS RunId, * FROM BakeRuns ORDER BY Timestamp DESC"),
        g.variable("SelectedRunId", "Int32"), g.variable("SelEndTime", "UtcTime"),
        *[g.variable(n, "String") for n in ("SelRun", "SelProduct", "SelStart", "SelEnd", "SelRunTime", "SelBake",
                                            "SelUnits", "SelMaxDev", "SelResult", "CountText", "StatsText",
                                            "ExportText", "Range7Style", "Range30Style", "RangeAllStyle",
                                            "ProductAllStyle")])
    alarms = g.folder("Alarms", g.variable("Count", "Int32"), g.variable("ButtonText", "String", "ALARMS"),
                      g.variable("ButtonStyle", "String", "AlarmIdle"),
                      *[g.variable(name + "State", "String", "NORMAL") for name, _ in ALARMS])
    hmi = g.folder("HMI", g.folder("Header", g.variable("ClockDate", "String"), g.variable("ClockTime", "String")),
                   g.folder("Status", g.variable("Running", "Boolean", True), g.variable("LineText", "String")),
                   schedule, history, alarms)
    return [zone_type, day_type, oven, hmi]


# --- store, logger, retentivity, alarms ----------------------------------------------------------

def table(m: g.Modules, name: str, columns: list[tuple[str, str]], limit: int) -> dict:
    cols = [{"Name": c, "Type": "StoreColumn", "DataType": dt} for c, dt in columns]
    return m.instance("SQLiteStoreTable", name, {"Columns": {"Children": cols}},
                      extra=[m.prop("Table", "RecordLimit", limit)])


def store(m: g.Modules) -> dict:
    runs = [("Timestamp", "UtcTime"), ("EndDay", "Int32"), ("RunNumber", "String"), ("Product", "String"),
            ("Started", "String"), ("DateText", "String"), ("StartText", "String"), ("EndText", "String"), ("RunMin", "Float"),
            ("BakeMin", "Float"), ("Units", "Int32"), ("MaxDev", "Float"), ("Result", "String"),
            ("Search", "String")] + [(f"{z}_Avg", "Float") for z in SHOWN]
    log = [("Timestamp", "UtcTime"), ("LocalTimestamp", "DateTime")]
    for z in SHOWN:
        log += [(f"{z}_PV", "Float"), (f"{z}_SP", "Float")]
    return m.instance("SQLiteStore", "OvenStore", {"Tables": {"Children": [
        table(m, "BakeRuns", runs, 20000), table(m, "OvenLogger", log, 400000)]}}, node_id=IDS("DataStores/OvenStore"))


def logged_variable(m: g.Modules, zone: str, member: str) -> dict:
    """One of the logger's VariablesToLog: a zone's PV or SP, logged as the column <zone>_<member>."""
    v = m.instance("VariableToLog", f"{zone}_{member}", extra=[g.link(f"../../../../Model/Oven/Zones/{zone}/{member}", 2)])
    v["DataType"] = "BaseDataType"
    return v


def logger(m: g.Modules) -> dict:
    to_log = [logged_variable(m, z, member) for z in SHOWN for member in ("PV", "SP")]
    return m.instance("DataLogger", "OvenLogger", {
        "SamplingMode": 1, "Store": {"Value": f"{ROOT}/DataStores/OvenStore", "Children": []},
        "VariablesToLog": {"Children": to_log},
    }, extra=[m.prop("DataLogger", "LogLocalTime", True), m.prop("DataLogger", "PollingPeriod", g.duration(2000))])


def retentivity(m: g.Modules) -> dict:
    node = {"Name": "Node1", "Type": "NodePointer", "DataType": "NodeId", "Value": model("HMI/Schedule"), "Children": [
        {"Name": "Kind", "Type": "PropertyType", "DataType": "NodeId"},
        {"Name": "NodeClass", "Type": "PropertyType", "DataType": "NodePointerNodeClass", "Value": 2}]}
    return m.instance("RetentivityStorage", "ScheduleRetentivity", {"Nodes": {"Children": [node]}})


def alarm(name: str, message: str, alarm_ns: int) -> dict:
    ref = lambda n: g.method_ref(alarm_ns, str(n))  # noqa: E731
    return {"Name": name, "Type": "OffNormalAlarmController", "Children": [
        g.variable("NormalStateValue", "Double"),
        {"Name": "InputValue", "Type": "BaseDataVariableType", "DataType": "Double",
         "Children": [g.link(f"../../../Model/Oven/Alarms/{name}", 2)]},
        {"Name": "LastEvent", "Type": "BaseDataVariableType", "DataType": "BaseDataType",
         "ValueRank": "OneDimension", "ArrayDimensions": [0]},
        *[ref(n) for n in ALARM_METHODS_FIRST],
        {"Name": "Message", "Type": "BaseDataVariableType", "DataType": "LocalizedText", "Value": g.text(message)},
        {"Name": "Active", "Type": "BaseDataVariableType", "DataType": "Boolean", "AccessLevel": "Read"},
        *[ref(n) for n in ALARM_METHODS_LAST],
    ]}


# --- style sheet -----------------------------------------------------------------------------------

def style(m: g.Modules, type_name: str, name: str, **props) -> dict:
    return {"Name": name, "Type": type_name, "Children": [m.prop(type_name, k, v) for k, v in props.items()]}


def button_style(m, name, color, border, text, pressed, size=16.0, family=CONDENSED, weight=SEMIBOLD) -> dict:
    return style(m, "ButtonStyle", name, Color=color, BorderColor=border, TextColor=text, PressedColor=pressed,
                 PressedBorderColor=border, PressedTextColor=text, FocusBorderColor=INK, FontFamily=family,
                 FontWeight=weight, FontSize=size, BorderWidth=1.0, Radius=0.0)


def style_sheet(m: g.Modules, original: str) -> dict:
    """The project's DefaultStyleSheet, rewritten with the design tokens. Every style folder that
    `new` wrote keeps its Default entry, of the same type."""
    sheet_id = re.search(r"Id: (g=[0-9a-f]{32})", original).group(1)
    folders = re.findall(r"^  - Name: (\w+Styles)\r?\n    Type: BaseObjectType\r?\n    Children:\r?\n"
                         r"    - Name: Default\r?\n      Type: (\w+)", original, flags=re.M)
    custom = {
        "ButtonStyles": [
            button_style(m, "Default", SHEET, INK, INK, GRID),
            button_style(m, "Line", NIGHT, ON_NIGHT, ON_NIGHT, NIGHT_2),
            button_style(m, "AlarmActive", ERR, ERR, ON_NIGHT, "#8E1B13", size=18.0),
            button_style(m, "AlarmIdle", NIGHT_2, NIGHT_RULE, ON_NIGHT_2, NIGHT, size=18.0),
            button_style(m, "Step", SHEET, INK, INK, GRID, size=26.0, family=TEXT, weight=400),
            button_style(m, "Chip", SHEET, GRID, MUTED, GRID, size=15.0),
            button_style(m, "ChipActive", INK, INK, SHEET, NIGHT_2, size=15.0),
        ],
        "NavigationPanelStyles": [style(
            m, "NavigationPanelStyle", "Default", InactiveColor=NIGHT_2, InactiveTextColor=ON_NIGHT_2,
            ActivePrimaryColor=SHEET, ActiveSecondaryColor=INK, BackgroundColor=NIGHT_2, FontFamily=CONDENSED,
            FontWeight=SEMIBOLD, FontSize=18.0, Radius=0.0, TabSpacing=0.0)],
        "SwitchStyles": [style(
            m, "SwitchStyle", "Default", BackgroundColor=PAPER, BorderColor=MUTED, BorderWidth=1.0, TextColor=MUTED,
            FontFamily=CONDENSED, FontWeight=SEMIBOLD, FontSize=15.0, ActiveBackgroundColor=ADD,
            ActiveBorderColor=ADD, ActiveTextColor=SHEET, ActiveHandleColor=SHEET, ActiveHandleBorderColor=ADD,
            HandleColor=SHEET, HandleBorderColor=MUTED, Radius=0.0, FocusBorderColor=INK)],
        "ChartStyles": [style(
            m, "ChartStyle", "Default", BackgroundColor=SHEET, TextColor=MUTED, GridColor=GRID, AxisColor=MUTED,
            FontFamily=MONO, FontSize=11.0)],
        "DataListStyles": [style(
            m, "DataListStyle", "Default", BackgroundColor=SHEET, TextColor=INK, BorderColor=GRID,
            HorizontalLineColor=GRID, VerticalLineColor=SHEET, SelectionBackgroundColor=INK,
            SelectionTextColor=SHEET, SelectionBorderColor=INK, HoverBackgroundColor=PAPER, HoverTextColor=INK,
            AlternateBackgroundColor="#F6F4EE", BorderWidth=1.0, FontFamily=TEXT, FontSize=14.0,
            HeaderBackgroundColor=PAPER, HeaderTextColor=MUTED, HeaderBorderColor=GRID, HeaderFontFamily=MONO,
            HeaderFontSize=11.0, Radius=0.0)],
        "InputBoxStyles": [style(
            m, "InputBoxStyle", "Default", BackgroundColor=SHEET, BorderColor=MUTED, TextColor=INK,
            FocusBackgroundColor=SHEET, FocusBorderColor=INK, FocusTextColor=INK, PlaceHolderColor=FAINT,
            FontFamily=TEXT, FontSize=15.0, BorderWidth=1.0, Radius=2.0)],
        "LabelStyles": [style(m, "LabelStyle", "Default", TextColor=INK, FontFamily=TEXT)],
        "RectangleStyles": [style(m, "RectangleStyle", "Default", BorderThickness=0.0, CornerRadius=0.0)],
        "ScreenStyles": [style(m, "ScreenStyle", "Default", BackgroundColor=PAPER)],
    }
    children = [m.prop("StyleSheet", k, v) for k, v in dict(
        AccentColor=INK, AccentTextColor=SHEET, AccentBorderColor=INK, WindowColor=NIGHT, ScreenColor=PAPER,
        InteractiveBackgroundColor=PAPER, InteractiveColor=SHEET, InteractiveColorGradientPercent=0.0, TextColor=INK, BorderColor=MUTED,
        DataInputBackgroundColor=SHEET, DataInputTextColor=INK, Radius=0.0, BorderWidth=1.0, FontFamily=TEXT,
        FocusColor=INK, InteractiveDropShadowSize=0.0, FocusGlowSize=0.0).items()]
    for folder, default_type in folders:
        entries = custom.get(folder) or [{"Name": "Default", "Type": default_type}]
        children.append({"Name": folder, "Type": "BaseObjectType", "Children": entries})
    return {"Name": "DefaultStyleSheet", "Id": sheet_id, "Type": "StyleSheet", "Children": children}


def replace_style_sheet(m: g.Modules, ui_yaml: Path) -> None:
    lines = ui_yaml.read_bytes().decode("utf-8").rstrip(g.CRLF).split(g.CRLF)
    start = lines.index("- Name: DefaultStyleSheet")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("- "))
    original = g.CRLF.join(lines[start:end])
    new = g.emit(style_sheet(m, original), 0, "- ")
    g.write(ui_yaml, g.CRLF.join(lines[:start] + new + lines[end:]) + g.CRLF)


# --- UI building blocks ------------------------------------------------------------------------------

class UI:
    """Builders for the screens. With `plain`, the same nodes are written without any styling
    (no colours, fonts or button styles), as an HMI looks before design work."""

    def __init__(self, m: g.Modules, ns: dict[str, int], plain: bool = False):
        self.m = m
        self.ns = ns
        self.plain = plain

    def place(self, x, y, w=None, h=None) -> list[dict]:
        out = [self.m.prop("Item", "LeftMargin", float(x)), self.m.prop("Item", "TopMargin", float(y))]
        if w is not None:
            out.append(self.m.prop("Item", "Width", float(w)))
        if h is not None:
            out.append(self.m.prop("Item", "Height", float(h)))
        return out

    def rect(self, name, at, fill=None, border=None, thickness=None, fill_link=None, visible=None) -> dict:
        kids = []
        if self.plain:
            fill = fill_link = border = None
        if fill is not None:
            kids.append(self.m.prop("Rectangle", "FillColor", fill))
        if fill_link is not None:
            kids.append(self.m.prop("Rectangle", "FillColor", linked=fill_link))
        if border is not None:
            kids.append(self.m.prop("Rectangle", "BorderColor", border))
            kids.append(self.m.prop("Rectangle", "BorderThickness", float(thickness or 1)))
        if visible is not None:
            kids.append(self.m.prop("Item", "Visible", linked=visible))
        return {"Name": name, "Type": "Rectangle", "Children": kids + self.place(*at)}

    def label(self, name, text=None, link=None, font=TEXT, size=15.0, color=INK, weight=None, at=(0, 0),
              align=None, visible=None) -> dict:
        kids = []
        if text is not None:
            kids.append(self.m.prop("Label", "Text", g.text(text)))
        if link is not None:
            kids.append(self.m.prop("Label", "Text", linked=link))
        if not self.plain:
            kids.append(self.m.prop("Label", "FontFamily", font))
        kids.append(self.m.prop("Label", "FontSize", float(size)))
        if not self.plain:
            kids.append(self.m.prop("Label", "TextColor", color))
        if weight is not None and not self.plain:
            kids.append(self.m.prop("Label", "FontWeight", weight))
        if align is not None:
            kids.append(self.m.prop("Label", "TextHorizontalAlignment", align))
        if visible is not None:
            kids.append(self.m.prop("Item", "Visible", linked=visible))
        return {"Name": name, "Type": "Label", "Children": kids + self.place(*at)}

    def caption(self, name, text, at, color=FAINT, link=None) -> dict:
        """A mono capital label, the design system's [label] style."""
        return self.label(name, None if link else text, link=link, font=MONO, size=11.0, color=color, at=at)

    def button(self, name, text=None, at=(0, 0, 120, 40), style_name=None, style_link=None, text_link=None,
               handler=None, enabled=None) -> dict:
        kids = []
        if text is not None:
            kids.append(self.m.prop("Button", "Text", g.text(text)))
        if text_link is not None:
            kids.append(self.m.prop("Button", "Text", linked=text_link))
        if self.plain:
            style_name = style_link = None
        if style_name is not None:
            kids.append(self.m.prop("Button", "Style", style_name))
        if style_link is not None:
            kids.append(self.m.prop("Button", "Style", linked=style_link))
        if enabled is not None:
            kids.append(self.m.prop("Item", "Enabled", linked=enabled))
        kids += self.place(*at)
        if handler is not None:
            kids.append(handler)
        return {"Name": name, "Type": "Button", "Children": kids}

    def set_handler(self, variable_path: str, data_type: str, value) -> dict:
        args = [{"Name": "VariableToModify", "Type": "BaseDataVariableType", "DataType": "VariablePointer",
                 "Value": variable_path},
                {"Name": "Value", "Type": "BaseDataVariableType", "DataType": data_type, "Value": value},
                {"Name": "ArrayIndex", "Type": "BaseDataVariableType", "DataType": "UInt32",
                 "ValueRank": "ScalarOrOneDimension"}]
        return g.click_handler(self.ns["FTOptix.CoreBase"], "Set", object_path="/Objects/Commands/VariableCommands",
                               arguments=args)

    def step_handler(self, variable_link: str, step: float) -> dict:
        args = [{"Name": "VariableToModify", "Type": "BaseDataVariableType", "DataType": "VariablePointer",
                 "Children": [g.link(variable_link)]},
                {"Name": "Delta", "Type": "BaseDataVariableType", "DataType": "Float", "Value": float(step)},
                {"Name": "ArrayIndex", "Type": "BaseDataVariableType", "DataType": "UInt32",
                 "ValueRank": "ScalarOrOneDimension"}]
        return g.click_handler(self.ns["FTOptix.CoreBase"], "Increment",
                               object_path="/Objects/Commands/VariableCommands", arguments=args)

    def pen(self, name: str, title: str, series: int, link: str | None) -> dict:
        # Plain: the ordinary colours an integrator picks in a hurry, not the validated series palette.
        colour = ([self.m.prop("TrendPen", "Color", PLAIN_PENS[series - 1])] if self.plain
                  else [self.m.prop("TrendPen", "Color", linked=palette(f"Series{series}"))])
        p = self.m.instance("TrendPen", name, {"Thickness": 2.0}, extra=([g.link(link)] if link else []) + colour + [
            self.m.prop("TrendPen", "Title", g.text(title))])
        p["DataType"] = "Float"
        return p

    def trend(self, name, model_ptr, query, pens, window_ms, at, follow=True, end_link=None) -> dict:
        values = {"Pens": {"Children": pens}, "XAxis/Window": g.duration(window_ms), "YAxis/AutoScale": True}
        if not follow:
            values["XAxis/Follow"] = False
        if end_link:
            values["XAxis/Time"] = {"Children": [g.link(end_link)]}
        extra = [self.m.member_instance("Trend", "Model", model_ptr), self.m.prop("Trend", "ReferenceTimeZone", 1)]
        if query:
            extra.append(self.m.prop("Trend", "Query", query))
        return self.m.instance("Trend", name, values, extra=extra + self.place(*at))

    def card(self, name, at) -> dict:
        return self.rect(name, at, fill=SHEET, border=GRID)

    def title_bar(self, title: str, note: str, note_link: str | None = None) -> list[dict]:
        return [self.rect("TitleBar", (0, 0, 1280, 52), fill=SHEET),
                self.rect("TitleRule", (0, 52, 1280, 1), fill=GRID),
                self.label("Title", title, font=CONDENSED, size=22.0, weight=SEMIBOLD, at=(24, 13)),
                self.caption("TitleNote", note, (900, 20, 356, 16), link=note_link)]


# --- project types: zone tile, day card ---------------------------------------------------------

def alias(name: str, kind: str) -> dict:
    return {"Name": name, "Type": "Alias", "DataType": "NodeId",
            "Children": [{"Name": "Kind", "Type": "PropertyType", "DataType": "NodeId", "Value": kind}]}


def zone_tile_type(u: UI) -> dict:
    z = "{Zone}"
    return {"Name": "ZoneTile", "Id": IDS("UI/ZoneTile"), "Supertype": "Panel", "Children": [
        u.m.prop("Item", "Width", 232.0), u.m.prop("Item", "Height", 152.0),
        alias("Zone", model("ZoneType")),
        u.rect("Card", (0, 0, 232, 152), fill=SHEET, border=GRID),
        u.rect("Stripe", (0, 0, 4, 152)),
        u.label("Name", link=f"{z}@BrowseName", font=MONO, size=12.0, color=MUTED, at=(20, 14)),
        u.caption("Caption", "ZONE TEMPERATURE", (64, 15)),
        u.label("PV", link=f"{z}/PV", font=CONDENSED, size=52.0, weight=SEMIBOLD, at=(18, 34)),
        u.label("PVUnit", "°F", size=18.0, color=MUTED, at=(120, 58)),
        u.caption("SPCaption", "SP", (20, 116)),
        u.label("SP", link=f"{z}/SP", size=15.0, color=INK, at=(42, 112)),
        u.caption("OutCaption", "OUT %", (120, 116)),
        u.label("Output", link=f"{z}/Output", size=15.0, color=INK, at=(172, 112)),
    ]}


def instance_of(type_node: dict, name: str, alias_name: str, alias_value: str, extra: list[dict]) -> dict:
    """An instance of a project type writes its alias (value and Kind), then every member of the type
    declared without ModellingRule, recursively (exp/2026-09-30-instance-membres)."""
    alias_type = next(c for c in type_node["Children"] if c.get("Type") == "Alias" and c["Name"] == alias_name)
    a = {**{k: v for k, v in alias_type.items() if k != "Children"}, "Value": alias_value,
         "Children": alias_type["Children"]}
    others = [c for c in type_node["Children"] if not (c.get("Type") == "Alias" and c["Name"] == alias_name)]
    return {"Name": name, "Type": type_node["Name"], "Children": [*extra, a, *g.mandatory_copy(others)]}


def zone_tile(u: UI, tile_type: dict, zone: str, series: int, x: float) -> dict:
    tile = instance_of(tile_type, f"Tile{zone}", "Zone", model(f"Oven/Zones/{zone}"), u.place(x, 72))
    if not u.plain:
        stripe = next(c for c in tile["Children"] if c.get("Name") == "Stripe")
        stripe.setdefault("Children", []).append(u.m.prop("Rectangle", "FillColor", linked=palette(f"Series{series}")))
    return tile


def day_card_type(u: UI) -> dict:
    d = "{Day}"
    switch = u.m.instance("Switch", "EnabledSwitch", {"Checked": {"Children": [g.link(f"{d}/Enabled", 2)]}},
                          extra=[u.m.prop("Switch", "CheckedText", g.text("ON")),
                                 u.m.prop("Switch", "UncheckedText", g.text("OFF")), *u.place(16, 76, 132, 40)])
    return {"Name": "DayCard", "Id": IDS("UI/DayCard"), "Supertype": "Panel", "Children": [
        u.m.prop("Item", "Width", 164.0), u.m.prop("Item", "Height", 372.0),
        alias("Day", model("DayType")),
        u.rect("Card", (0, 0, 164, 372), fill=SHEET, border=GRID),
        u.rect("TodayMark", (0, 0, 164, 4), fill=INK, visible=f"{d}/IsToday"),
        u.label("DayName", link=f"{d}/DayName", font=CONDENSED, size=20.0, weight=SEMIBOLD, at=(16, 18)),
        {**u.caption("Today", "[TODAY]", (16, 46), color=INK),
         "Children": u.caption("Today", "[TODAY]", (16, 46), color=INK)["Children"]
         + [u.m.prop("Item", "Visible", linked=f"{d}/IsToday")]},
        switch,
        u.caption("StartCaption", "PREHEAT START", (16, 144)),
        u.label("StartText", link=f"{d}/StartText", font=CONDENSED, size=34.0, weight=SEMIBOLD, at=(16, 164)),
        u.button("Earlier", "‹", (16, 222, 62, 48), style_name="Step", handler=u.step_handler(f"{d}/StartMinutes@NodeId", -15),
                 enabled=f"{d}/Enabled"),
        u.button("Later", "›", (86, 222, 62, 48), style_name="Step", handler=u.step_handler(f"{d}/StartMinutes@NodeId", 15),
                 enabled=f"{d}/Enabled"),
        u.caption("StepCaption", "STEP 15 MIN", (16, 280)),
        u.rect("Rule", (16, 306, 132, 1), fill=GRID),
        u.label("ReadyText", link=f"{d}/ReadyText", size=15.0, color=MUTED, at=(16, 322)),
    ]}


def day_card(u: UI, card_type: dict, day: int, x: float) -> dict:
    return instance_of(card_type, f"Day{day}Card", "Day", model(f"HMI/Schedule/Day{day}"), u.place(x, 108))


# --- screens ---------------------------------------------------------------------------------------

def screen(name: str, *children: dict) -> dict:
    return {"Name": name, "Id": IDS(f"UI/Screens/{name}"), "Supertype": "Screen", "Children": list(children)}


def overview(u: UI, tile_type: dict) -> dict:
    tiles = [zone_tile(u, tile_type, z, i + 1, 24 + i * 248) for i, z in enumerate(SHOWN)]
    live = [u.pen(f"{z}_PV", f"{z} PV", i + 1, f"{ROOT}/Loggers/OvenLogger/VariablesToLog/{z}_PV/LastValue")
            for i, z in enumerate(SHOWN)]
    info = [
        u.card("BeltCard", (24, 244, 376, 364)),
        u.caption("BeltCaption", "[BELT]", (44, 262)),
        u.label("BakeTime", link=model("Oven/Belt/BakeTimeMin"), font=CONDENSED, size=44.0, weight=SEMIBOLD,
                at=(44, 280)),
        u.label("BakeUnit", "min bake", size=16.0, color=MUTED, at=(130, 302)),
        u.label("Speed", link=model("Oven/Belt/SpeedFtMin"), size=16.0, at=(44, 340)),
        u.label("SpeedUnit", "ft/min belt speed", size=16.0, color=MUTED, at=(84, 340)),
        u.rect("Rule1", (44, 380, 336, 1), fill=GRID),
        u.caption("ProductCaption", "[PRODUCT]", (44, 396)),
        u.label("Product", link=model("Oven/Product/Name"), font=CONDENSED, size=26.0, weight=SEMIBOLD, at=(44, 414)),
        u.rect("Rule2", (44, 460, 336, 1), fill=GRID),
        u.caption("RunCaption", "[CURRENT RUN]", (44, 476)),
        u.label("RunNumber", link=model("Oven/Run/Number"), font=MONO, size=16.0, at=(44, 496)),
        u.label("RunStartCaption", "started", size=15.0, color=MUTED, at=(44, 528)),
        u.label("RunStart", link=model("Oven/Run/StartText"), size=15.0, at=(104, 528)),
        u.label("Units", link=model("Oven/Run/Units"), font=CONDENSED, size=26.0, weight=SEMIBOLD, at=(44, 556)),
        u.label("UnitsCaption", "pans out", size=15.0, color=MUTED, at=(116, 564)),
    ]
    chart = [
        u.card("TrendCard", (416, 244, 840, 364)),
        u.caption("TrendCaption", "[ZONE TEMPERATURES]  LAST 15 MIN  ·  °F", (436, 262)),
        u.trend("LiveTrend", f"{ROOT}/Loggers/OvenLogger", None, live, 15 * 60_000, (432, 282, 808, 314)),
    ]
    return screen("Overview", *u.title_bar("OVERVIEW", "[LIVE]  UPDATED EVERY SECOND"), *tiles, *info, *chart)


def schedule(u: UI, card_type: dict) -> dict:
    cards = [day_card(u, card_type, d, 24 + (d - 1) * 176) for d in range(1, 8)]
    return screen("Schedule", *u.title_bar("PREHEAT SCHEDULE", "", note_link=model("HMI/Schedule/TimeZoneText")),
                  u.rect("NextDot", (24, 76, 10, 10), fill=ADD),
                  u.label("NextStart", link=model("HMI/Schedule/NextStartText"), font=CONDENSED, size=17.0,
                          weight=SEMIBOLD, at=(42, 70)),
                  u.caption("WarmupNote", "WARM-UP 75 MIN BEFORE THE LINE IS READY", (900, 74)),
                  *cards,
                  u.caption("ScheduleNote", "PREHEAT BRINGS EVERY ZONE TO SETPOINT BEFORE THE FIRST PANS ENTER THE OVEN.",
                            (24, 500)))


def history(u: UI) -> dict:
    h = model("HMI/History")
    chips = [
        u.button("Last7", "7 DAYS", (348, 66, 92, 36), style_link=f"{h}/Range7Style",
                 handler=u.set_handler(f"{h}/RangeDays", "Int32", 7)),
        u.button("Last30", "30 DAYS", (444, 66, 92, 36), style_link=f"{h}/Range30Style",
                 handler=u.set_handler(f"{h}/RangeDays", "Int32", 30)),
        u.button("AllRuns", "ALL", (540, 66, 72, 36), style_link=f"{h}/RangeAllStyle",
                 handler=u.set_handler(f"{h}/RangeDays", "Int32", 0)),
        u.button("AllProducts", "ALL PRODUCTS", (628, 66, 132, 36), style_link=f"{h}/ProductAllStyle",
                 handler=u.set_handler(f"{h}/Product", "String", "")),
    ]
    search = {"Name": "Search", "Type": "TextBox", "Children": [
        {"Name": "Text", "Type": "BaseDataVariableType", "DataType": "LocalizedText",
         "Children": [g.link(f"{h}/Search", 2)]},
        u.m.prop("TextBox", "PlaceholderText", g.text("Search run or product")),
        u.m.prop("TextBox", "ValueChangeBehaviour", 1),
        *u.place(24, 66, 308, 36)]}
    export = u.button("ExportCsv", "EXPORT CSV", (1136, 66, 120, 36),
                      handler=g.click_handler(u.ns["FTOptix.CoreBase"], "Export",
                                              object_path=f"{ROOT}/NetLogic/CsvExport"))
    # 732 px for zones 1 to 4: the grid (836 px, scroll bar included) keeps room for one more zone column.
    columns = [("RUN", "RunNumber", 80), ("STARTED", "Started", 120), ("PRODUCT", "Product", 150),
               ("BAKE MIN", "BakeMin", 68)]
    columns += [(f"{z} AVG", f"{z}_Avg", 56) for z in SHOWN]
    columns += [("RESULT", "Result", 90)]
    grid = runs_grid(u, columns, (24, 118, 836, 452))
    run_pens = [u.pen(f"{z}_PV", f"{z} PV", i + 1, None) for i, z in enumerate(SHOWN)]
    detail = [
        u.card("DetailCard", (872, 118, 384, 490)),
        u.label("SelRun", link=f"{h}/SelRun", font=CONDENSED, size=26.0, weight=SEMIBOLD, at=(892, 130)),
        u.label("SelProduct", link=f"{h}/SelProduct", size=14.0, color=MUTED, at=(892, 164)),
        u.caption("StartCaption", "START", (892, 194)), u.label("SelStart", link=f"{h}/SelStart", at=(892, 210)),
        u.caption("EndCaption", "END", (992, 194)), u.label("SelEnd", link=f"{h}/SelEnd", at=(992, 210)),
        u.caption("RunTimeCaption", "RUN TIME", (1092, 194)),
        u.label("SelRunTime", link=f"{h}/SelRunTime", at=(1092, 210)),
        u.caption("BakeCaption", "BAKE", (892, 240)), u.label("SelBake", link=f"{h}/SelBake", at=(892, 256)),
        u.caption("MaxDevCaption", "MAX DEV", (992, 240)), u.label("SelMaxDev", link=f"{h}/SelMaxDev", at=(992, 256)),
        u.caption("ResultCaption", "RESULT", (1092, 240)), u.label("SelResult", link=f"{h}/SelResult", at=(1092, 256)),
        u.trend("RunTrend", f"{ROOT}/DataStores/OvenStore", "SELECT * FROM OvenLogger", run_pens, 2 * 3_600_000,
                (884, 290, 360, 306), follow=False, end_link=f"{h}/SelEndTime"),
    ]
    return screen("BakeHistory", *u.title_bar("BAKE HISTORY", "[NEWEST FIRST]", note_link=None),
                  search, *chips, export, grid, *detail,
                  u.label("Stats", link=f"{h}/StatsText", size=14.0, color=MUTED, at=(24, 584)),
                  u.label("Count", link=f"{h}/CountText", font=MONO, size=11.0, color=FAINT, at=(700, 588)),
                  u.label("ExportText", link=f"{h}/ExportText", font=MONO, size=11.0, color=FAINT, at=(872, 614)))


def grid_column(u: UI, title: str, field: str, width: float) -> dict:
    """A label column of the bake history grid, reading `field` of each row."""
    col = u.m.instance("DataGridColumn", f"{field}Column", {"Title": g.text(title), "Width": float(width)})
    cell = u.m.instance("DataGridLabelItemTemplate", "DataItemTemplate",
                        {"Text": {"Children": [g.link("{Item}/" + field)]}})
    col["Children"] = [cell if c.get("Name") == "DataItemTemplate" else c for c in col["Children"]]
    return col


def runs_grid(u: UI, columns: list[tuple[str, str, float]], at: tuple) -> dict:
    """The bake history grid: one label column per (title, field, width), fed by the bound query."""
    cols = [grid_column(u, title, field, width) for title, field, width in columns]
    return u.m.instance("DataGrid", "RunsGrid", {
        "Model": f"{ROOT}/DataStores/OvenStore",
        "Query": {"Children": [g.link(model("HMI/History/GridQuery"))]},
        "Columns": {"Children": cols},
    }, extra=[*u.place(*at), {"Name": "HistorySelection", "Type": "NetLogic"}])


def alarms_screen(u: UI) -> dict:
    rows = []
    for i, (name, message) in enumerate(ALARMS):
        y = 84 + i * 72
        rows += [
            u.card(f"{name}Row", (24, y, 1232, 60)),
            u.rect(f"{name}Mark", (24, y, 6, 60), fill=ERR, visible=model(f"Oven/Alarms/{name}")),
            u.label(f"{name}Message", message, font=CONDENSED, size=20.0, weight=SEMIBOLD, at=(52, y + 17)),
            u.label(f"{name}State", link=model(f"HMI/Alarms/{name}State"), font=MONO, size=13.0, at=(1100, y + 22)),
        ]
    return screen("Alarms", *u.title_bar("ALARMS", "[ACTIVE CONDITIONS]  REFRESHED EVERY SECOND"), *rows)


# --- window: header, navigation, footer ------------------------------------------------------------

def window_children(u: UI, screens: list[str]) -> list[dict]:
    hh = model("HMI/Header")
    header = [
        u.rect("Chrome", (0, 0, 1280, 800), fill=NIGHT),
        u.rect("LogoFrame", (24, 16, 32, 32), fill=NIGHT, border=ON_NIGHT, thickness=2),
        u.rect("LogoHearth", (31, 33, 18, 8), fill=ON_NIGHT),
        u.label("Brand", BAKERY.upper(), font=CONDENSED, size=20.0, color=ON_NIGHT, weight=SEMIBOLD, at=(68, 10)),
        u.caption("Line", "[LINE 2]  TUNNEL OVEN", (68, 38), color=ON_NIGHT_2),
        u.caption("OperatorCaption", "OPERATOR", (1000, 14), color=ON_NIGHT_2),
        u.label("Operator", "Not signed in", size=15.0, color=ON_NIGHT, at=(1000, 30)),
        u.rect("ClockRule", (1136, 12, 1, 40), fill=NIGHT_RULE),
        u.label("ClockDate", link=f"{hh}/ClockDate", font=MONO, size=11.0, color=ON_NIGHT_2, at=(1152, 14)),
        u.label("ClockTime", link=f"{hh}/ClockTime", font=MONO, size=15.0, color=ON_NIGHT, at=(1152, 30)),
    ]
    items = []
    titles = {"Overview": "OVERVIEW", "Schedule": "SCHEDULE", "BakeHistory": "BAKE HISTORY", "Alarms": "ALARMS"}
    for i, s in enumerate(screens):
        item = u.m.instance("NavigationPanelItem", f"Tab{i}", {
            "Title": g.text(titles[s]), "Panel": {"Value": f"{ROOT}/UI/Screens/{s}", "Children": []}})
        g.find(item["Children"], "Panel/Kind")["Value"] = "/Types/ObjectTypes/BaseObjectType/BaseUIObject/Item/Container"
        items.append(item)
    nav = u.m.instance("NavigationPanel", "Nav", {"Panels": {"Children": items}}, extra=[
        u.m.prop("NavigationPanel", "TabHeight", 48.0), u.m.prop("NavigationPanel", "TabWidth", 176.0),
        *u.place(0, 64, 1280, 680)])
    st = model("HMI/Status")
    al = model("HMI/Alarms")
    footer = [
        u.rect("FooterRule", (0, 744, 1280, 1), fill=NIGHT_RULE),
        {"Name": "StatusDot", "Type": "Ellipse", "Children": [
            u.m.prop("Ellipse", "FillColor", ADD), u.m.prop("Item", "Visible", linked=f"{st}/Running"),
            *u.place(24, 766, 12, 12)]},
        u.label("LineStatus", link=f"{st}/LineText", font=CONDENSED, size=17.0, color=ON_NIGHT, weight=SEMIBOLD,
                at=(46, 760)),
        u.button("AlarmsButton", at=(1096, 752, 160, 40), text_link=f"{al}/ButtonText", style_link=f"{al}/ButtonStyle",
                 handler=g.click_handler(u.ns["FTOptix.CoreBase"], "ChangePanelByTabIndex",
                                         object_link="../../../../../Nav@NodeId",
                                         arguments=[{"Name": "Index", "Type": "BaseDataVariableType",
                                                     "DataType": "Int32", "Value": 3},
                                                    {"Name": "AliasNode", "Type": "BaseDataVariableType",
                                                     "DataType": "NodeId"}])),
    ]
    return [*header, nav, *footer]


def palette_folder() -> dict:
    tokens = [("Night", NIGHT), ("Night2", NIGHT_2), ("NightRule", NIGHT_RULE), ("OnNight", ON_NIGHT),
              ("OnNight2", ON_NIGHT_2), ("Paper", PAPER), ("Sheet", SHEET), ("Ink", INK), ("Muted", MUTED),
              ("Faint", FAINT), ("Grid", GRID), ("Running", ADD), ("Alarm", ERR)]
    tokens += [(f"Series{i + 1}", c) for i, c in enumerate(SERIES)]
    return {"Name": "Palette", "Type": "FolderType", "Children": [g.variable(n, "Color", v) for n, v in tokens]}


# --- plain variant: the same project as an integrator lays it out before any design work ---------

def plain_value(u: UI, name: str, caption: str, link: str, x: float, y: float, unit: str = "") -> list[dict]:
    out = [u.label(f"{name}Caption", caption + ":", size=16.0, at=(x, y)),
           u.label(name, link=link, size=16.0, at=(x + 170, y))]
    if unit:
        out.append(u.label(f"{name}Unit", unit, size=16.0, at=(x + 240, y)))
    return out


def plain_overview(u: UI) -> dict:
    rows = []
    for i, z in enumerate(SHOWN):
        y = 70 + i * 40
        zp = model(f"Oven/Zones/{z}")
        rows += [u.label(f"{z}Name", f"Zone {z[1:]}", size=16.0, at=(20, y)),
                 u.label(f"{z}PV", link=f"{zp}/PV", size=16.0, at=(120, y)),
                 u.label(f"{z}Unit", "°F", size=16.0, at=(170, y)),
                 u.label(f"{z}SPCaption", "SP:", size=16.0, at=(230, y)),
                 u.label(f"{z}SP", link=f"{zp}/SP", size=16.0, at=(270, y)),
                 u.label(f"{z}OutCaption", "Output %:", size=16.0, at=(350, y)),
                 u.label(f"{z}Out", link=f"{zp}/Output", size=16.0, at=(440, y))]
    info = (plain_value(u, "BakeTime", "Bake time", model("Oven/Belt/BakeTimeMin"), 640, 70, "min")
            + plain_value(u, "Speed", "Belt speed", model("Oven/Belt/SpeedFtMin"), 640, 110, "ft/min")
            + plain_value(u, "Product", "Product", model("Oven/Product/Name"), 640, 150)
            + plain_value(u, "Run", "Run", model("Oven/Run/Number"), 640, 190)
            + plain_value(u, "Units", "Units", model("Oven/Run/Units"), 640, 230))
    live = [u.pen(f"{z}_PV", f"{z} PV", i + 1, f"{ROOT}/Loggers/OvenLogger/VariablesToLog/{z}_PV/LastValue")
            for i, z in enumerate(SHOWN)]
    return screen("Overview", u.label("Title", "Overview", size=22.0, at=(20, 16)), *rows, *info,
                  u.trend("LiveTrend", f"{ROOT}/Loggers/OvenLogger", None, live, 15 * 60_000, (20, 280, 1240, 420)))


def plain_schedule(u: UI) -> dict:
    s = model("HMI/Schedule")
    rows = []
    for d in range(1, 8):
        y = 110 + (d - 1) * 66
        day = f"{s}/Day{d}"
        switch = u.m.instance("Switch", f"Day{d}Switch", {"Checked": {"Children": [g.link(f"{day}/Enabled", 2)]}},
                              extra=u.place(160, y - 6, 120, 40))
        rows += [u.label(f"Day{d}Name", link=f"{day}/DayName", size=16.0, at=(20, y)), switch,
                 u.label(f"Day{d}Start", link=f"{day}/StartText", size=16.0, at=(310, y)),
                 u.button(f"Day{d}Earlier", "<", (430, y - 6, 50, 40),
                          handler=u.step_handler(f"{day}/StartMinutes@NodeId", -15)),
                 u.button(f"Day{d}Later", ">", (490, y - 6, 50, 40),
                          handler=u.step_handler(f"{day}/StartMinutes@NodeId", 15)),
                 u.label(f"Day{d}Ready", link=f"{day}/ReadyText", size=16.0, at=(570, y))]
    return screen("Schedule", u.label("Title", "Preheat schedule", size=22.0, at=(20, 16)),
                  u.label("NextStart", link=f"{s}/NextStartText", size=16.0, at=(20, 64)),
                  u.label("TimeZone", link=f"{s}/TimeZoneText", size=16.0, at=(640, 64)), *rows)


def plain_history(u: UI) -> dict:
    h = model("HMI/History")
    search = {"Name": "Search", "Type": "TextBox", "Children": [
        {"Name": "Text", "Type": "BaseDataVariableType", "DataType": "LocalizedText",
         "Children": [g.link(f"{h}/Search", 2)]},
        u.m.prop("TextBox", "ValueChangeBehaviour", 1), *u.place(20, 60, 280, 36)]}
    buttons = [
        u.button("Last7", "7 days", (320, 60, 90, 36), handler=u.set_handler(f"{h}/RangeDays", "Int32", 7)),
        u.button("Last30", "30 days", (420, 60, 90, 36), handler=u.set_handler(f"{h}/RangeDays", "Int32", 30)),
        u.button("AllRuns", "All", (520, 60, 70, 36), handler=u.set_handler(f"{h}/RangeDays", "Int32", 0)),
        u.button("ExportCsv", "Export CSV", (1140, 60, 120, 36),
                 handler=g.click_handler(u.ns["FTOptix.CoreBase"], "Export", object_path=f"{ROOT}/NetLogic/CsvExport")),
    ]
    columns = [("Run", "RunNumber", 90), ("Started", "Started", 130), ("Product", "Product", 160),
               ("Bake min", "BakeMin", 70)] + [(f"{z} avg", f"{z}_Avg", 60) for z in SHOWN] + [("Result", "Result", 90)]
    run_pens = [u.pen(f"{z}_PV", f"{z} PV", i + 1, None) for i, z in enumerate(SHOWN)]
    detail = (plain_value(u, "SelRun", "Selected run", f"{h}/SelRun", 860, 110)
              + plain_value(u, "SelStart", "Start", f"{h}/SelStart", 860, 140)
              + plain_value(u, "SelEnd", "End", f"{h}/SelEnd", 860, 170)
              + plain_value(u, "SelBake", "Bake", f"{h}/SelBake", 860, 200)
              + plain_value(u, "SelResult", "Result", f"{h}/SelResult", 860, 230))
    return screen("BakeHistory", u.label("Title", "Bake history", size=22.0, at=(20, 16)), search, *buttons,
                  runs_grid(u, columns, (20, 110, 820, 560)), *detail,
                  u.trend("RunTrend", f"{ROOT}/DataStores/OvenStore", "SELECT * FROM OvenLogger", run_pens,
                          2 * 3_600_000, (860, 270, 400, 400), follow=False, end_link=f"{h}/SelEndTime"),
                  u.label("Stats", link=f"{h}/StatsText", size=16.0, at=(20, 684)),
                  u.label("Count", link=f"{h}/CountText", size=16.0, at=(600, 684)))


def plain_alarms(u: UI) -> dict:
    rows = []
    for i, (name, message) in enumerate(ALARMS):
        y = 70 + i * 40
        rows += [u.label(f"{name}Message", message, size=16.0, at=(20, y)),
                 u.label(f"{name}State", link=model(f"HMI/Alarms/{name}State"), size=16.0, at=(400, y))]
    return screen("Alarms", u.label("Title", "Alarms", size=22.0, at=(20, 16)), *rows,
                  *plain_value(u, "Count", "Active alarms", model("HMI/Alarms/Count"), 20, 200))


def plain_window(u: UI, screens: list[str]) -> list[dict]:
    items = []
    for i, s in enumerate(screens):
        item = u.m.instance("NavigationPanelItem", f"Tab{i}", {
            "Title": g.text(s), "Panel": {"Value": f"{ROOT}/UI/Screens/{s}", "Children": []}})
        g.find(item["Children"], "Panel/Kind")["Value"] = "/Types/ObjectTypes/BaseObjectType/BaseUIObject/Item/Container"
        items.append(item)
    return [u.m.instance("NavigationPanel", "Nav", {"Panels": {"Children": items}}, extra=[
        u.m.prop("Item", "HorizontalAlignment", STRETCH), u.m.prop("Item", "VerticalAlignment", STRETCH)])]


# --- assembly --------------------------------------------------------------------------------------

def build(studio: Path, skeleton: Path, out: Path, theme: str = "exploded") -> Path:
    stage = Path(tempfile.mkdtemp(prefix="ovenline2-"))
    try:
        work = stage / PROJECT
        g.copy_skeleton(skeleton, work, IDS)
        ns = g.set_dependencies(work / f"{PROJECT}.optix", studio, MODULES, IDS)
        m = g.Modules(studio)
        m.ns = ns
        u = UI(m, ns, plain=theme == "plain")
        nodes = work / "Nodes"
        g.add_children(nodes / "Model" / "Model.yaml", model_nodes())
        g.add_children(nodes / "DataStores" / "DataStores.yaml", [store(m)])
        g.add_children(nodes / "Loggers" / "Loggers.yaml", [logger(m)])
        g.add_children(nodes / "Retentivity" / "Retentivity.yaml", [retentivity(m)])
        g.add_children(nodes / "Alarms" / "Alarms.yaml", [alarm(n, msg, ns["FTOptix.Alarm"]) for n, msg in ALARMS])
        g.add_children(nodes / "NetLogic" / "NetLogic.yaml", [{"Name": n, "Type": "NetLogic"} for n in NETLOGIC])

        if u.plain:
            screens = [plain_overview(u), plain_schedule(u), plain_history(u), plain_alarms(u)]
        else:
            tile_type, card_type = zone_tile_type(u), day_card_type(u)
            screens = [overview(u, tile_type), schedule(u, card_type), history(u), alarms_screen(u)]
        g.write(nodes / "UI" / "Screens" / "Screens.yaml",
                g.dump({"Name": "Screens", "Type": "ScreensCategoryFolder", "Children": screens}))
        ui_yaml = nodes / "UI" / "UI.yaml"
        names = [s["Name"] for s in screens]
        if u.plain:
            g.add_to_main_window(ui_yaml, plain_window(u, names), [])
        else:
            replace_style_sheet(m, ui_yaml)
            g.add_to_main_window(ui_yaml, window_children(u, names), [palette_folder(), tile_type, card_type])

        sources = {f"{n}.cs": (HERE / "netlogic" / f"{n}.cs").read_text(encoding="utf-8")
                   for n in NETLOGIC + ["HistorySelection", "OvenZones"]}
        g.write_solution(work, PROJECT, sources, studio, IDS)
        if not u.plain:
            fonts = work / "ProjectFiles" / "Font"
            fonts.mkdir(parents=True, exist_ok=True)
            for f in FONTS:
                shutil.copyfile(HERE / "fonts" / f, fonts / f)
            for f in ["OFL-Barlow.txt", "OFL-FragmentMono.txt"]:   # text: CRLF, as git checks it out
                g.write(fonts / f, (HERE / "fonts" / f).read_text(encoding="utf-8"))

        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(work, out)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=HERE / PROJECT)
    ap.add_argument("--studio", type=Path)
    ap.add_argument("--skeleton", type=Path, help="existing output of `FTOptixStudio.com new` for this project")
    ap.add_argument("--theme", choices=["exploded", "plain"], default="exploded",
                    help="plain: the same model and logic with a basic default-widget UI, before any design work")
    args = ap.parse_args()
    studio = args.studio or g.find_studio()
    temp = None
    try:
        if args.skeleton:
            skeleton = args.skeleton
        else:
            temp = Path(tempfile.mkdtemp(prefix="ovenline2-new-"))
            skeleton = g.cli_new(studio, PROJECT, temp, WINDOW)
        build(studio, skeleton, args.out, args.theme)
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
    print(f"built {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
