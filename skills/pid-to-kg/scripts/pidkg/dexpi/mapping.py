"""Map a knowledge graph onto the DEXPI 2.0.1 plant model.

Classes are chosen from each node's ``kind``, not its stored ``dexpi_class``,
so a ``graph.json`` written by an older version exports correctly.

Every graph node and edge receives exactly one outcome:

* ``mapped``: has a direct DEXPI counterpart
* ``transformed``: represented, but in a different structure
* ``derived_omitted``: not written because it follows from what is written
* ``unrepresented``: DEXPI output does not carry it; listed in the report

DEXPI objects the drawing did not state (nozzles, tees, couplings) are counted
as synthesized and reported, so nobody mistakes them for extracted facts.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .. import __version__
from ..models import Edge, KnowledgeGraph, Node
from ..ontology import COMPONENT_DEXPI, EQUIPMENT_DEXPI, UNCLASSIFIED_COMPONENT, UNCLASSIFIED_EQUIPMENT
from .metamodel import LOCAL, Metamodel
from .writer import DateTime, EnumValue, MultiLang, Obj

MAPPED, TRANSFORMED, OMITTED, UNREPRESENTED = "mapped", "transformed", "derived_omitted", "unrepresented"
OUTCOMES = (MAPPED, TRANSFORMED, OMITTED, UNREPRESENTED)

LOCAL_CLASSES = {
    UNCLASSIFIED_EQUIPMENT: "Plant/ProcessEquipment.ProcessEquipment",
    UNCLASSIFIED_COMPONENT: "Plant/Piping.PipingComponent",
}

_LOCATION = {
    "field": "Field", "local_panel": "ControlPanel", "behind_panel": "ControlPanel",
    "main_panel": "CentralLocation", "dcs": "CentralLocation", "plc": "CentralLocation",
    "scada": "CentralLocation", "sis": "CentralLocation", "computer": "CentralLocation",
}
_SIGNAL = {
    "electrical": "ElectricalSignalConveying", "pneumatic": "PneumaticSignalConveying",
    "hydraulic": "HydraulicSignalConveying", "capillary": "CapillarySignalConveying",
}
_FAIL = {"FO": "FailOpen", "FC": "FailClose", "FL": "FailRetainPosition"}


@dataclass
class MappingResult:
    root: Obj
    model_name: str
    model_uri: str
    local_classes: dict[str, str]
    node_outcomes: dict[str, str]
    edge_outcomes: dict[str, str]
    synthesized: Counter
    warnings: dict[str, list[str]] = field(default_factory=dict)


class Mapper:
    def __init__(self, kg: KnowledgeGraph, mm: Metamodel):
        self.kg, self.mm = kg, mm
        self.nodes: dict[str, Node] = {n.id: n for n in kg.nodes}
        self.node_outcomes: dict[str, str] = {}
        self.edge_outcomes: dict[str, str] = {}
        self.synthesized: Counter = Counter()
        self.warnings: dict[str, list[str]] = defaultdict(list)
        self.local_used: dict[str, str] = {}
        self._ids: set[str] = set()
        self.objs: dict[str, Obj] = {}
        self.run_segments: dict[str, Obj] = {}
        self.systems: dict[str, Obj] = {}
        self.pif: dict[str, Obj] = {}
        self.psgf: dict[str, list[Obj]] = defaultdict(list)
        self.actuating: dict[str, Obj] = {}
        self.electrical: dict[str, Obj] = {}
        self.instrument_nozzles: dict[str, Obj] = {}
        self.root = Obj("Core/EngineeringModel")
        self.plant = Obj("Plant/PlantModel", self.new_id("PlantModel"))

    # ------------------------------------------------------------- helpers

    def new_id(self, prefix: str, base: str = "") -> str:
        slug = re.sub(r"[^A-Za-z0-9_]+", "_", f"{prefix}_{base}" if base else prefix).strip("_")
        candidate, n = slug, 1
        while candidate in self._ids:
            n += 1
            candidate = f"{slug}_{n}"
        self._ids.add(candidate)
        return candidate

    def obj(self, simple: str, base: str = "") -> Obj:
        if simple in LOCAL_CLASSES:
            self.local_used[simple] = LOCAL_CLASSES[simple]
            return Obj(f"/{simple}", self.new_id(simple, base))
        return Obj(self.mm.resolve(simple), self.new_id(simple, base))

    def warn(self, code: str, item: str) -> None:
        if item not in self.warnings[code]:
            self.warnings[code].append(item)

    def is_pipe(self, nid: str) -> bool:
        return self.nodes[nid].type == "PipeSegment"

    def is_inline(self, n: Node) -> bool:
        return n.type == "Instrument" and bool(n.attributes.get("inline"))

    def type_of(self, obj: Obj) -> str:
        return f"{LOCAL}{obj.type}" if obj.type.startswith("/") else obj.type

    # ----------------------------------------------------------------- run

    def run(self) -> MappingResult:
        kg = self.kg
        self.root.set("ExportDateTime", DateTime(kg.generated_at))
        self.root.set("OriginatingSystemName", "pid-to-kg")
        self.root.set("OriginatingSystemVendorName", "pid-to-kg")
        self.root.set("OriginatingSystemVersion", __version__)
        self.root.add("ConceptualModel", self.plant)
        for n in kg.nodes:
            if n.type == "Plant":
                self.node_outcomes[n.id] = MAPPED
            elif n.type == "Sheet":
                self.node_outcomes[n.id] = UNREPRESENTED
                self.warn("sheet_not_represented", n.label)
                if n.attributes.get("drawing_type") == "pfd":
                    self.warn("pfd_sheet_included", n.label)
            if n.status in ("future", "demolish"):
                self.warn("status_not_represented", f"{n.label} ({n.status})")
        self._items()
        self._piping()
        self._off_page()
        self._orphans()
        self._instruments()
        self._signals()
        self._loops()
        self._remaining_edges()
        missing = [n.id for n in kg.nodes if n.id not in self.node_outcomes] + \
                  [e.id for e in kg.edges if e.id not in self.edge_outcomes]
        if missing:
            raise RuntimeError(f"mapping left {len(missing)} graph items unaccounted for: {missing[:5]}")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", kg.plant.id).strip("_") or "plant"
        return MappingResult(self.root, slug, f"urn:pid-to-kg:{slug}", self.local_used,
                             self.node_outcomes, self.edge_outcomes, self.synthesized,
                             {k: v for k, v in self.warnings.items() if v})

    # --------------------------------------------------------------- items

    def _items(self) -> None:
        for n in self.kg.nodes:
            if n.type == "Equipment":
                simple = EQUIPMENT_DEXPI.get(n.kind or "", UNCLASSIFIED_EQUIPMENT)
                o = self.obj(simple, n.id).set("TagName", n.tag)
                if n.name:
                    o.set("EquipmentDescription", MultiLang(n.name))
                self.plant.add("TaggedPlantItems", o)
                self.objs[n.id] = o
                self.node_outcomes[n.id] = TRANSFORMED if simple == UNCLASSIFIED_EQUIPMENT else MAPPED
                if simple == UNCLASSIFIED_EQUIPMENT:
                    self.warn("unclassified_equipment", f"{n.label} (kind: {n.kind})")
            elif n.type == "Component":
                simple = COMPONENT_DEXPI.get(n.kind or "", UNCLASSIFIED_COMPONENT)
                o = self.obj(simple, n.id).set("PipingComponentName", n.tag or n.name)
                self.objs[n.id] = o
                self.node_outcomes[n.id] = TRANSFORMED if simple == UNCLASSIFIED_COMPONENT else MAPPED
                if simple == UNCLASSIFIED_COMPONENT:
                    self.warn("unclassified_component", f"{n.label} (kind: {n.kind})")
            elif self.is_inline(n):
                self.objs[n.id] = self.obj("InlineMeasuringElement", n.id).set("PipingComponentName", n.tag or n.label)
            elif n.type == "OffPageConnector":
                simple = "FlowOutPipeOffPageConnector" if n.attributes.get("direction") == "out" \
                    else "FlowInPipeOffPageConnector"
                o = self.obj(simple, n.id)
                text = [n.label]
                if n.attributes.get("other_drawing"):
                    text.append(f"drawing {n.attributes['other_drawing']}")
                if n.attributes.get("other_item"):
                    text.append(f"item {n.attributes['other_item']}")
                o.set("PipeConnectorDescription", MultiLang("; ".join(text)))
                self.objs[n.id] = o
                self.node_outcomes[n.id] = MAPPED

    # -------------------------------------------------------------- piping

    def _run_segment(self, n: Node) -> Obj:
        run = n.id.rsplit(".", 1)[0]
        if run in self.run_segments:
            return self.run_segments[run]
        key = n.tag or run
        system = self.systems.get(key)
        if system is None:
            system = self.obj("PipingNetworkSystem", key).set("LineNumber", n.attributes.get("line_number"))
            self.plant.add("PipingNetworkSystems", system)
            self.systems[key] = system
        seg = system.add("Segments", self.obj("PipingNetworkSegment", run))
        seg.set("SegmentNumber", run)
        seg.set("NominalDiameterRepresentation", n.attributes.get("size"))
        seg.set("FluidCode", n.attributes.get("service"))
        self.run_segments[run] = seg
        return seg

    def _fixture(self, simple: str, seg: Obj, base: str) -> Obj:
        self.synthesized[simple] += 1
        return seg.add("Items", self.obj(simple, base))

    def _piping(self) -> None:
        pipes = [n for n in self.kg.nodes if n.type == "PipeSegment"]
        state = {n.id: {"src": None, "tgt": None, "tees": [], "stubs": []} for n in pipes}
        segs = {n.id: self._run_segment(n) for n in pipes}

        def tee(sid: str) -> tuple:
            fixture = self._fixture("PipeTee", segs[sid], sid)
            state[sid]["tees"].append(("fixture", fixture, None))
            return ("fixture", fixture, None)

        branches: list[Edge] = []
        self.direct: list[Edge] = []
        for e in self.kg.edges:
            if e.type != "FLOWS_TO":
                continue
            s_pipe, t_pipe = self.is_pipe(e.source), self.is_pipe(e.target)
            if e.attributes.get("flow_direction") == "inferred":
                self.warn("flow_direction_inferred", f"{self.nodes[e.source].label} -> {self.nodes[e.target].label}")
            if s_pipe and t_pipe:
                branches.append(e)
            elif t_pipe:
                token = ("node", e.source, e.id)
                if state[e.target]["src"] is None:
                    state[e.target]["src"] = token
                else:
                    state[e.target]["stubs"].append((token, tee(e.target)))
            elif s_pipe:
                token = ("node", e.target, e.id)
                if state[e.source]["tgt"] is None:
                    state[e.source]["tgt"] = token
                else:
                    state[e.source]["stubs"].append((tee(e.source), token))
            else:
                self.direct.append(e)

        for e in branches:
            a, b = state[e.source], state[e.target]
            self.edge_outcomes[e.id] = TRANSFORMED
            if a["tgt"] is None and b["src"] is None:
                coupling = ("fixture", self._fixture("PipeCoupling", segs[e.source], e.source), None)
                a["tgt"] = b["src"] = coupling
            elif b["src"] is None:
                b["src"] = tee(e.source)
            elif a["tgt"] is None:
                a["tgt"] = tee(e.target)
            else:
                state[e.source]["stubs"].append((tee(e.source), tee(e.target)))
            self.warn("junction_synthesized", f"{self.nodes[e.source].label} -> {self.nodes[e.target].label}")

        for n in pipes:
            st, seg = state[n.id], segs[n.id]
            chain = [st["src"], *st["tees"], st["tgt"]]
            made = []
            for a, b in zip(chain, chain[1:]):
                pipe = seg.add("Connections", self.obj("Pipe", n.id))
                self._attach(pipe, "Source", a, seg, n)
                self._attach(pipe, "Target", b, seg, n)
                made.append(pipe)
            for a, b in st["stubs"]:
                stub = seg.add("Connections", self.obj("Pipe", f"{n.id}_branch"))
                self.synthesized["Pipe (branch stub)"] += 1
                self._attach(stub, "Source", a, seg, n)
                self._attach(stub, "Target", b, seg, n)
            for prop in ("SourceItem", "SourceNode"):
                if prop in made[0].references and prop not in seg.references:
                    seg.ref(prop, made[0].references[prop][0])
            for prop in ("TargetItem", "TargetNode"):
                if prop in made[-1].references:
                    seg.references[prop] = [made[-1].references[prop][0]]
            self.node_outcomes[n.id] = TRANSFORMED

    def _attach(self, pipe: Obj, side: str, token, seg: Obj, pipe_node: Node) -> None:
        if token is None:
            self.warn("open_pipe_end", f"{pipe_node.label} ({side.lower()})")
            return
        kind, ref, edge_id = token
        if kind == "fixture":
            item = ref
        else:
            n = self.nodes[ref]
            item = self._item_for(n, side, seg)
            if item is None:
                self.edge_outcomes[edge_id] = UNREPRESENTED
                self.warn("connection_not_represented", f"{n.label} at {side.lower()} of {pipe_node.label}")
                return
            self.edge_outcomes[edge_id] = TRANSFORMED
        node = item.add("Nodes", self.obj("PipingNode", item.id))
        pipe.ref(f"{side}Item", item).ref(f"{side}Node", node)

    def _item_for(self, n: Node, side: str, seg: Obj) -> Optional[Obj]:
        if n.type == "Equipment":
            self.synthesized["ProcessNozzle"] += 1
            self.warn("nozzle_synthesized", n.label)
            return self.objs[n.id].add("Nozzles", self.obj("ProcessNozzle", n.id))
        if n.type == "Component" or self.is_inline(n):
            o = self.objs[n.id]
            if o.parent is None:
                seg.add("Items", o)
            return o
        if n.type == "OffPageConnector":
            o = self.objs[n.id]
            wanted = "FlowInPipeOffPageConnector" if side == "Source" else "FlowOutPipeOffPageConnector"
            if not o.type.endswith(wanted):
                return None
            if o.parent is None:
                seg.add("Items", o)
            return o
        if n.type == "Boundary" and n.attributes.get("direction") != "area":
            simple = "FlowInPipeOffPageConnector" if side == "Source" else "FlowOutPipeOffPageConnector"
            o = seg.add("Items", self.obj(simple, n.id))
            o.set("PipeConnectorDescription", MultiLang(n.label))
            self.node_outcomes[n.id] = TRANSFORMED
            return o
        return None

    def _off_page(self) -> None:
        for e in getattr(self, "direct", []):
            a, b = self.nodes[e.source], self.nodes[e.target]
            if a.type == b.type == "OffPageConnector" and "ConnectorReference" not in self.objs[a.id].components:
                for x, y in ((a, b), (b, a)):
                    ref = self.objs[x.id].add("ConnectorReference",
                                              self.obj("PipeOffPageConnectorObjectReference", x.id))
                    ref.ref("ReferencedConnector", self.objs[y.id])
                self.edge_outcomes[e.id] = TRANSFORMED
            else:
                self.edge_outcomes[e.id] = UNREPRESENTED
                self.warn("connection_not_represented", f"{a.label} -> {b.label}")

    def _orphans(self) -> None:
        loose = [n for n in self.kg.nodes
                 if (n.type in ("Component", "OffPageConnector") or self.is_inline(n))
                 and self.objs[n.id].parent is None]
        if loose:
            system = self.plant.add("PipingNetworkSystems", self.obj("PipingNetworkSystem", "unconnected_items"))
            seg = system.add("Segments", self.obj("PipingNetworkSegment", "unconnected_items"))
            self.synthesized["PipingNetworkSystem (unconnected items)"] += 1
            for n in loose:
                seg.add("Items", self.objs[n.id])
                self.warn("item_not_connected", n.label)
        for n in self.kg.nodes:
            if n.type == "Boundary" and n.id not in self.node_outcomes:
                self.node_outcomes[n.id] = UNREPRESENTED
                self.warn("boundary_not_represented", f"{n.label} ({n.attributes.get('direction')})")

    # ----------------------------------------------------- instrumentation

    def _instruments(self) -> None:
        instruments = [n for n in self.kg.nodes if n.type == "Instrument"]
        for n in instruments:
            a = n.attributes
            letters = a.get("isa_letters") or ""
            pif = self.plant.add("ProcessInstrumentationFunctions", self.obj("ProcessInstrumentationFunction", n.id))
            pif.set("ProcessInstrumentationFunctionNumber", n.tag or n.label)
            pif.set("ProcessInstrumentationFunctionCategory", letters[:1])
            pif.set("ProcessInstrumentationFunctions", letters[1:])
            if a.get("location") in _LOCATION:
                pif.set("Location", EnumValue(f"Plant/Enumerations.LocationClassification.{_LOCATION[a['location']]}"))
            self.pif[n.id] = pif
            self.node_outcomes[n.id] = TRANSFORMED if self.is_inline(n) else MAPPED
            if self.is_inline(n):
                element = self.objs[n.id]
                system = self.plant.add("MeasuringSystems", self.obj("MeasuringSystem", n.id))
                system.set("MeasuringSystemNumber", n.tag or n.label)
                ref = system.add("MeasuringElement", self.obj("InlineMeasuringElementReference", n.id))
                ref.ref("InlineMeasuringElement", element)
                self._psgf(n, element).ref("Systems", system)

        for e in self.kg.edges:
            if e.type != "MEASURES":
                continue
            source = self.nodes[e.source]
            if source.type != "Instrument":
                self.edge_outcomes[e.id] = UNREPRESENTED
                self.warn("measurement_not_represented", f"{source.label} -> {self.nodes[e.target].label}")
            elif self.is_inline(source):
                self.edge_outcomes[e.id] = TRANSFORMED
            else:
                location = self._sensing_location(e)
                if location is None:
                    self.edge_outcomes[e.id] = UNREPRESENTED
                    self.warn("measurement_not_represented", f"{source.label} -> {self.nodes[e.target].label}")
                else:
                    self._psgf(source, location)
                    self.edge_outcomes[e.id] = TRANSFORMED

    def _psgf(self, n: Node, location: Optional[Obj]) -> Obj:
        psgf = self.pif[n.id].add("ProcessSignalGeneratingFunctions",
                                  self.obj("ProcessSignalGeneratingFunction", n.id))
        psgf.set("ProcessSignalGeneratingFunctionNumber", n.tag or n.label)
        if location is not None:
            psgf.ref("SensingLocation", location)
        self.psgf[n.id].append(psgf)
        return psgf

    def _sensing_location(self, e: Edge) -> Optional[Obj]:
        target = self.nodes[e.target]
        if target.type == "PipeSegment":
            self.warn("sensing_location_coarsened",
                      f"{self.nodes[e.source].label} on {target.label} (exact tap is in graph.json)")
            return self.run_segments[target.id.rsplit('.', 1)[0]]
        if target.type == "Component" or self.is_inline(target):
            return self.objs[target.id]
        if target.type == "Equipment":
            if target.id not in self.instrument_nozzles:
                self.synthesized["InstrumentNozzle"] += 1
                self.warn("nozzle_synthesized", target.label)
                self.instrument_nozzles[target.id] = self.objs[target.id].add(
                    "Nozzles", self.obj("InstrumentNozzle", target.id))
            return self.instrument_nozzles[target.id]
        return None

    def _actuating_function(self, target: Node, owner: Obj) -> Obj:
        if target.id in self.actuating:
            return self.actuating[target.id]
        function = owner.add("ActuatingFunctions", self.obj("ActuatingFunction", target.id))
        function.set("ActuatingFunctionNumber", target.tag or target.label)
        valve = self.objs[target.id]
        if valve.parent is not None and valve.parent.type.endswith("PipingNetworkSegment"):
            function.ref("ActuatingLocation", valve.parent)
        system = self.plant.add("ActuatingSystems", self.obj("ActuatingSystem", target.id))
        system.set("ActuatingSystemNumber", target.tag or target.label)
        function.ref("Systems", system)
        if self.mm.is_a(self.type_of(valve), "Plant/Piping.OperatedValve"):
            system.add("OperatedValveReference", self.obj("OperatedValveReference", target.id)).ref("Valve", valve)
        else:
            self.warn("actuated_item_not_operated_valve", target.label)
        fail = target.attributes.get("fail_position")
        actuator = target.attributes.get("actuator")
        if fail or actuator not in (None, "manual", "none", "self"):
            device = system.add("ControlledActuator", self.obj("ControlledActuator", target.id))
            if fail in _FAIL:
                device.set("FailAction", EnumValue(f"Plant/Enumerations.FailActionClassification.{_FAIL[fail]}"))
            elif fail:
                self.warn("fail_position_not_represented", f"{target.label} ({fail})")
        self.actuating[target.id] = function
        return function

    def _electrical_function(self, target: Node, owner: Obj) -> Obj:
        if target.id not in self.electrical:
            function = owner.add("ActuatingElectricalFunctions", self.obj("ActuatingElectricalFunction", target.id))
            function.set("ActuatingElectricalFunctionNumber", target.tag or target.label)
            self.electrical[target.id] = function
            self.warn("equipment_actuation_by_tag", target.label)
        return self.electrical[target.id]

    def _signals(self) -> None:
        for e in self.kg.edges:
            if e.type != "SIGNAL_TO":
                continue
            a, b = self.nodes[e.source], self.nodes[e.target]
            if a.type != "Instrument" or b.type not in ("Instrument", "Component", "Equipment"):
                self.edge_outcomes[e.id] = UNREPRESENTED
                self.warn("signal_not_represented", f"{a.label} -> {b.label}")
                continue
            owner = self.pif[a.id]
            measuring = e.attributes.get("purpose") == "measurement" and bool(self.psgf.get(a.id))
            source = self.psgf[a.id][0] if measuring else owner
            if b.type == "Instrument":
                target = self.pif[b.id]
                holder = target
                outcome = MAPPED
            elif b.type == "Component":
                target = self._actuating_function(b, owner)
                holder, outcome = owner, TRANSFORMED
            else:
                target = self._electrical_function(b, owner)
                holder, outcome = owner, TRANSFORMED
            line = holder.add("SignalConveyingFunctions",
                              self.obj("MeasuringLineFunction" if measuring else "SignalLineFunction", e.id))
            line.ref("Source", source).ref("Target", target)
            medium = e.attributes.get("signal_type")
            if medium in _SIGNAL:
                line.set("SignalConveyingType", EnumValue(
                    f"Plant/Enumerations.SignalConveyingTypeClassification.{_SIGNAL[medium]}"))
            elif medium and medium != "unknown":
                self.warn("signal_type_not_represented", f"{a.label} -> {b.label} ({medium})")
            self.edge_outcomes[e.id] = outcome

    def _loops(self) -> None:
        loops: dict[str, Obj] = {}
        for n in self.kg.nodes:
            if n.type == "ControlLoop":
                loop = self.plant.add("InstrumentationLoopFunctions", self.obj("InstrumentationLoopFunction", n.id))
                loop.set("InstrumentationLoopFunctionNumber", n.label)
                loops[n.id] = loop
                self.node_outcomes[n.id] = MAPPED
                if n.kind and n.kind != "control":
                    self.warn("loop_type_not_represented", f"{n.label} ({n.kind})")
        for e in self.kg.edges:
            if e.type != "MEMBER_OF":
                continue
            if e.source in self.pif and e.target in loops:
                loops[e.target].ref("ProcessInstrumentationFunctions", self.pif[e.source])
                self.edge_outcomes[e.id] = MAPPED
            elif e.source in self.actuating or e.source in self.electrical:
                self.edge_outcomes[e.id] = TRANSFORMED
            else:
                self.edge_outcomes[e.id] = UNREPRESENTED
                self.warn("loop_member_not_represented",
                          f"{self.nodes[e.source].label} in {self.nodes[e.target].label}")

    def _remaining_edges(self) -> None:
        for e in self.kg.edges:
            if e.id in self.edge_outcomes:
                continue
            a, b = self.nodes[e.source], self.nodes[e.target]
            if e.type in ("FEEDS", "ACTUATES"):
                self.edge_outcomes[e.id] = OMITTED
            elif e.type == "CASCADES_TO":
                self.edge_outcomes[e.id] = OMITTED
                self.warn("cascade_role_implicit", f"{a.label} -> {b.label}")
            elif e.type == "TRIPS":
                self.edge_outcomes[e.id] = UNREPRESENTED
                self.warn("trip_not_represented", f"{a.label} -> {b.label}")
            elif e.type == "PART_OF" and a.type == "Sheet":
                self.edge_outcomes[e.id] = UNREPRESENTED
            elif e.type == "PART_OF" and a.type == "Equipment" and b.type == "Equipment":
                child, parent = self.objs[a.id], self.objs[b.id]
                if self.mm.is_a(self.type_of(child), "Plant/ProcessEquipment.Agitator") and \
                        self.mm.is_a(self.type_of(parent), "Plant/ProcessEquipment.Vessel") and \
                        "Agitator" not in parent.references:
                    parent.ref("Agitator", child)
                    self.edge_outcomes[e.id] = MAPPED
                else:
                    self.edge_outcomes[e.id] = UNREPRESENTED
                    self.warn("mounting_not_represented", f"{a.label} on {b.label}")
            else:
                self.edge_outcomes[e.id] = UNREPRESENTED
                self.warn("relationship_not_represented", f"{e.type}: {a.label} -> {b.label}")


def map_graph(kg: KnowledgeGraph, mm: Metamodel) -> MappingResult:
    return Mapper(kg, mm).run()
