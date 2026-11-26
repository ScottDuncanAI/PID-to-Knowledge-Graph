"""DEXPI 2.0.1 export: default build output, validation, mapping and failure handling."""

import importlib
import subprocess
import sys
from collections import defaultdict

import pytest
from lxml import etree

from conftest import FIXTURES, SCRIPTS
from pidkg.dexpi import REPORT_NAME, XML_NAME, export_dexpi
from pidkg.dexpi.mapping import OUTCOMES, map_graph
from pidkg.dexpi.metamodel import Metamodel
from pidkg.dexpi.validate import validate_bytes
from pidkg.ontology import COMPONENT_DEXPI, EQUIPMENT_DEXPI, UNCLASSIFIED_COMPONENT, UNCLASSIFIED_EQUIPMENT

REFERENCE = FIXTURES / "dexpi" / "reference_pid.xml"


@pytest.fixture(scope="module")
def mm():
    return Metamodel()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The normal build, run exactly as the skill runs it: no DEXPI flag."""
    out = tmp_path_factory.mktemp("build") / "kg"
    r = subprocess.run([sys.executable, str(SCRIPTS / "build_kg.py"), str(FIXTURES / "mini_plant"),
                        "--out", str(out), "--plant-name", "Mini"], capture_output=True, text=True)
    return out, r


# ------------------------------------------------------------ default output

def test_normal_build_writes_validated_dexpi(built):
    out, r = built
    assert r.returncode == 0, r.stderr
    for f in ("graph.json", "graph.duckdb", "summary.md", "qa_report.md", XML_NAME, REPORT_NAME):
        assert (out / f).exists(), f
    check = validate_bytes((out / XML_NAME).read_bytes())
    assert check.ok, check.xsd_errors + check.model_errors
    assert "dexpi: valid DEXPI 2.0.1" in r.stdout
    report = (out / REPORT_NAME).read_text(encoding="utf-8")
    assert "**Status: VALID" in report and "| Official XSD | passed |" in report


def test_dexpi_failure_leaves_core_build_untouched(built, tmp_path):
    """Without lxml the DEXPI export fails, but the build must behave exactly as before DEXPI."""
    block = tmp_path / "block"
    block.mkdir()
    (block / "sitecustomize.py").write_text(
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'lxml' or name.startswith('lxml.'):\n"
        "            raise ImportError('lxml blocked for test')\n"
        "sys.meta_path.insert(0, _Block())\n", encoding="utf-8")
    out = tmp_path / "kg"
    env = {**__import__("os").environ, "PYTHONPATH": str(block)}
    r = subprocess.run([sys.executable, str(SCRIPTS / "build_kg.py"), str(FIXTURES / "mini_plant"),
                        "--out", str(out), "--plant-name", "Mini"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dexpi: FAILED" in r.stdout
    assert not (out / XML_NAME).exists()
    good, _ = built
    for f in ("summary.md",):
        assert (out / f).read_text(encoding="utf-8") == (good / f).read_text(encoding="utf-8")
    for f in ("graph.json", "graph.duckdb", "qa_report.md"):
        assert (out / f).exists(), f


def test_export_command_reuses_same_exporter(built, tmp_path):
    out, _ = built
    copy = tmp_path / "kg"
    copy.mkdir()
    (copy / "graph.json").write_bytes((out / "graph.json").read_bytes())
    r = subprocess.run([sys.executable, str(SCRIPTS / "export.py"), str(copy), "dexpi"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # The export timestamp comes from the graph, so the same graph gives the same bytes.
    assert (copy / XML_NAME).read_bytes() == (out / XML_NAME).read_bytes()


# ------------------------------------------------------------------ validator

def test_validator_accepts_official_reference_pid():
    check = validate_bytes(REFERENCE.read_bytes())
    assert check.ok, (check.xsd_errors + check.model_errors)[:5]
    assert check.objects > 1000


@pytest.mark.parametrize("old,new,expect", [
    ('type="Plant/ProcessEquipment.Tank"', 'type="Plant/ProcessEquipment.Tankk"', "class does not exist"),
    ('type="Plant/ProcessEquipment.Tank"', 'type="Plant/ProcessEquipment.ProcessEquipment"', "abstract"),
    ('property="TagName"', 'property="TagNombre"', "has no property"),
    ('objects="#ProcessInstrumentationFunction1"', 'objects="#Missing1"', "missing object"),
    ('data="Plant/Enumerations.LocationClassification.Field"',
     'data="Plant/Enumerations.LocationClassification.Moon"', "unknown enumeration"),
    ('<String>P&amp;ID Toolbox</String>', '<String>P&amp;ID Toolbox</String><String>again</String>',
     "at most 1"),
])
def test_validator_rejects_model_violations(old, new, expect):
    text = REFERENCE.read_text(encoding="utf-8")
    assert old in text
    check = validate_bytes(text.replace(old, new, 1).encode("utf-8"))
    assert not check.ok
    assert any(expect in e for e in check.model_errors), check.model_errors[:5]


def test_validator_reports_xsd_violations():
    text = REFERENCE.read_text(encoding="utf-8").replace('<Model name="example"', '<Model', 1)
    assert validate_bytes(text.encode("utf-8")).xsd_errors


# ------------------------------------------------------------------- mapping

def test_every_kind_maps_to_a_valid_concrete_class(mm):
    for table, base, fallback in ((EQUIPMENT_DEXPI, "Plant/ProcessEquipment.ProcessEquipment", UNCLASSIFIED_EQUIPMENT),
                                  (COMPONENT_DEXPI, "Plant/Piping.PipingComponent", UNCLASSIFIED_COMPONENT)):
        for kind, simple in table.items():
            if simple == fallback:
                continue
            q = mm.resolve(simple)
            assert not mm.classes[q].abstract, (kind, simple)
            assert mm.is_a(q, base), (kind, simple)


def test_every_node_and_edge_is_accounted_for(mini_kg, mm):
    m = map_graph(mini_kg, mm)
    assert set(m.node_outcomes) == {n.id for n in mini_kg.nodes}
    assert set(m.edge_outcomes) == {e.id for e in mini_kg.edges}
    assert set(m.node_outcomes.values()) | set(m.edge_outcomes.values()) <= set(OUTCOMES)


def _flow_graph(xml: bytes):
    """Item-to-item flow read back from DEXPI pipes, nozzles mapped to their equipment."""
    root = etree.fromstring(xml)
    by_id = {o.get("id"): o for o in root.iter("Object")}

    def owner(obj_id):
        o = by_id[obj_id]
        if o.get("type").endswith("Nozzle"):
            o = o.getparent().getparent()
        tag = o.find("Data[@property='TagName']/String")
        if tag is None:
            tag = o.find("Data[@property='PipingComponentName']/String")
        return tag.text if tag is not None else o.get("id")

    adj = defaultdict(set)
    for pipe in root.iter("Object"):
        if pipe.get("type") != "Plant/Piping.Pipe":
            continue
        refs = {r.get("property"): r.get("objects").lstrip("#") for r in pipe.findall("References")}
        if "SourceItem" in refs and "TargetItem" in refs:
            adj[owner(refs["SourceItem"])].add(owner(refs["TargetItem"]))
    for conn in root.iter("Object"):
        ref = conn.find("Components[@property='ConnectorReference']/Object/References")
        if conn.get("type").endswith("FlowOutPipeOffPageConnector") and ref is not None:
            adj[conn.get("id")].add(ref.get("objects").lstrip("#"))
    return root, adj


def _reachable(adj, start):
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def test_round_trip_keeps_flow_and_loops(built):
    out, _ = built
    root, adj = _flow_graph((out / XML_NAME).read_bytes())
    assert {"P-101", "FV-101", "TK-200"} <= _reachable(adj, "TK-100")
    assert "TK-100" in _reachable(adj, "P-101")  # minimum-flow recirculation through the tee
    loops = {o.find("Data[@property='InstrumentationLoopFunctionNumber']/String").text:
             o.find("References").get("objects") for o in root.iter("Object")
             if o.get("type") == "Plant/Instrumentation.InstrumentationLoopFunction"}
    assert "FIC_101" in loops["F-101"] and "LIC_200" in loops["L-200"]
    valve_ref = [o for o in root.iter("Object") if o.get("type") == "Plant/Instrumentation.OperatedValveReference"]
    assert valve_ref and valve_ref[0].find("References").get("objects") == "#OperatedValve_FV_101"


def test_unclassified_kinds_and_stale_dexpi_class_still_validate(mini_kg, tmp_path):
    kg = mini_kg.model_copy(deep=True)
    for n in kg.nodes:
        n.dexpi_class = "Column"  # a name that is not in DEXPI 2.0.1
        if n.id == "TK-200":
            n.kind = "package_unit"
        if n.id == "P01.V3":
            n.kind = "drain"
    result = export_dexpi(kg, tmp_path)
    assert result.ok, result.errors
    warnings = result.mapping.warnings
    assert warnings["unclassified_equipment"] and warnings["unclassified_component"]
    assert b'<ConcreteClass name="UnclassifiedEquipment"' in (tmp_path / XML_NAME).read_bytes()


# ---------------------------------------------------------- failure handling

def test_failed_validation_removes_stale_file(mini_kg, tmp_path, monkeypatch):
    import pidkg.dexpi.validate as validate

    (tmp_path / XML_NAME).write_text("stale output from an earlier build", encoding="utf-8")
    real = validate.validate_bytes

    def broken(xml, mm=None):
        r = real(xml, mm)
        r.model_errors.append("injected failure")
        return r

    monkeypatch.setattr(validate, "validate_bytes", broken)
    result = export_dexpi(mini_kg, tmp_path)
    assert not result.ok
    assert not (tmp_path / XML_NAME).exists()
    assert not (tmp_path / f"{XML_NAME}.tmp").exists()
    report = (tmp_path / REPORT_NAME).read_text(encoding="utf-8")
    assert "**Status: FAILED**" in report and "injected failure" in report


def test_build_keeps_other_outputs_when_export_crashes(tmp_path, monkeypatch, capsys):
    import pidkg.dexpi.mapping as mapping

    def crash(kg, mm):
        raise RuntimeError("injected crash")

    monkeypatch.setattr(mapping, "map_graph", crash)
    build_kg = importlib.import_module("build_kg")
    out = tmp_path / "kg"
    out.mkdir()
    (out / XML_NAME).write_text("stale", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["build_kg.py", str(FIXTURES / "mini_plant"), "--out", str(out)])
    assert build_kg.main() == 0  # a DEXPI failure never fails the core build
    for f in ("graph.json", "graph.duckdb", "summary.md", "qa_report.md", REPORT_NAME):
        assert (out / f).exists(), f
    assert not (out / XML_NAME).exists()
    assert "dexpi: FAILED" in capsys.readouterr().out


def test_graph_dexpi_class_values_are_unchanged(mini_kg):
    """The DEXPI exporter must not change the dexpi_class values existing graph users rely on."""
    from pidkg.ontology import GRAPH_COMPONENT_CLASS, GRAPH_EQUIPMENT_CLASS

    segs = {n.dexpi_class for n in mini_kg.nodes if n.type == "PipeSegment"}
    assert segs == {"PipingNetworkSegment"}
    assert GRAPH_EQUIPMENT_CLASS["column"] == "Column" and GRAPH_EQUIPMENT_CLASS["other"] == "Equipment"
    assert GRAPH_COMPONENT_CLASS["blind"] == "Blind" and GRAPH_COMPONENT_CLASS["other"] == "PipingComponent"
    assert set(GRAPH_EQUIPMENT_CLASS) == set(EQUIPMENT_DEXPI)
    assert set(GRAPH_COMPONENT_CLASS) == set(COMPONENT_DEXPI)
