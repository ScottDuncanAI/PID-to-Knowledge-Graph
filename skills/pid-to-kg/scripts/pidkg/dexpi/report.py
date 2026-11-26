"""Write ``dexpi_export_report.md``: what was exported and what was not."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from .mapping import OUTCOMES

if TYPE_CHECKING:
    from . import DexpiResult

WARNING_TEXT = {
    "nozzle_synthesized": "Nozzles were added because DEXPI connects pipes and instruments to nozzles. They were not read from the drawing",
    "junction_synthesized": "A tee or coupling was added where lines join. Its position along the pipe is not known",
    "sensing_location_coarsened": "The sensing point is recorded on the line segment, not the exact tap",
    "unclassified_equipment": "No DEXPI class fits. Exported as the extension class UnclassifiedEquipment, which other tools may not recognize",
    "unclassified_component": "No DEXPI class fits. Exported as the extension class UnclassifiedPipingComponent, which other tools may not recognize",
    "equipment_actuation_by_tag": "DEXPI has no direct link from a signal to this equipment. The target is identified by tag only",
    "actuated_item_not_operated_valve": "The actuated item is not an operated valve, so no valve link was written",
    "flow_direction_inferred": "Flow direction was inferred, not drawn. DEXPI records it as a normal direction",
    "open_pipe_end": "A pipe end has no connected item",
    "connection_not_represented": "A process connection could not be written",
    "item_not_connected": "The item has no pipe connection and was placed in the 'unconnected_items' system",
    "measurement_not_represented": "The measured location could not be written",
    "signal_not_represented": "The signal could not be written",
    "signal_type_not_represented": "DEXPI has no value for this signal medium",
    "fail_position_not_represented": "DEXPI has no value for this fail position",
    "loop_type_not_represented": "The loop is exported, but its type is not",
    "loop_member_not_represented": "The item is not linked to the loop",
    "cascade_role_implicit": "The controller signal is exported, but the master/slave cascade role is not explicit",
    "trip_not_represented": "The trip signal is exported, but its safety meaning is not",
    "mounting_not_represented": "The mounting relationship could not be written",
    "boundary_not_represented": "The boundary could not be written",
    "relationship_not_represented": "The relationship could not be written",
    "sheet_not_represented": "Sheet identity and title-block data are not in the semantic export. See graph.json",
    "pfd_sheet_included": "This PFD sheet is included, so PFD connections may overlap P&ID detail",
    "status_not_represented": "Status is not exported, so this item looks like normal plant equipment",
}


def _esc(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_report(result: "DexpiResult") -> str:
    out: list[str] = []
    w = out.append
    verdict = {"passed": "VALID", "passed_with_warnings": "VALID, WITH MAPPING WARNINGS", "failed": "FAILED"}[result.status]
    w("# DEXPI export report")
    w("")
    w(f"**Status: {verdict}**")
    w("")
    if result.status == "failed":
        w("`graph.dexpi.xml` was **not** written for this build, and any earlier copy was removed. "
          "The other graph outputs are unaffected.")
    else:
        w("`graph.dexpi.xml` is the DEXPI 2.0.1 native XML export of this build.")
    w("")
    w("| Check | Result |")
    w("|---|---|")
    w(f"| Official XSD | {result.xsd_status} |")
    w(f"| DEXPI metamodel (classes, properties, multiplicity, types, references) | {result.model_status} |")
    w(f"| DEXPI objects written | {result.objects} |")
    w(f"| Source graph QA errors | {result.qa_errors} |")
    w(f"| `graph.json` SHA-256 | `{result.graph_sha256 or 'n/a'}` |")
    w(f"| `graph.dexpi.xml` SHA-256 | `{result.xml_sha256 or 'not written'}` |")
    w("")
    if result.qa_errors:
        w(f"> The source graph has {result.qa_errors} open QA error(s). Fix them in `qa_report.md` and rebuild "
          "before relying on this file.")
        w("")
    if result.errors:
        w("## Errors")
        w("")
        for e in result.errors:
            w(f"- {_esc(e)}")
        w("")
    w("This is a semantic export: plant objects, connectivity and instrumentation, with no drawing "
      "graphics, so CAD tools will not redraw it as a P&ID. It is machine-extracted, is not a certified "
      "DEXPI export, and needs engineering review before use in design, operations or safety work. "
      "Confidence scores and drawing provenance stay in `graph.json`.")
    w("")
    if result.mapping is None:
        return "\n".join(out) + "\n"

    m = result.mapping
    w("## Coverage")
    w("")
    w("| Outcome | Nodes | Edges |")
    w("|---|---|---|")
    nodes, edges = Counter(m.node_outcomes.values()), Counter(m.edge_outcomes.values())
    labels = {"mapped": "Mapped directly", "transformed": "Represented in a different structure",
              "derived_omitted": "Omitted, derivable from the export", "unrepresented": "Not represented"}
    for o in OUTCOMES:
        w(f"| {labels[o]} | {nodes.get(o, 0)} | {edges.get(o, 0)} |")
    w(f"| **Total** | **{sum(nodes.values())}** | **{sum(edges.values())}** |")
    w("")
    if m.synthesized:
        w("## Objects added to satisfy the DEXPI model")
        w("")
        w("These were not read from the drawings.")
        w("")
        w("| Object | Count |")
        w("|---|---|")
        for name, count in sorted(m.synthesized.items()):
            w(f"| {name} | {count} |")
        w("")
    if m.warnings:
        w("## Mapping warnings")
        w("")
        for code, items in sorted(m.warnings.items()):
            w(f"### `{code}` ({len(items)})")
            w("")
            w(WARNING_TEXT.get(code, code) + ".")
            w("")
            w(", ".join(f"`{_esc(i)}`" for i in items))
            w("")
    return "\n".join(out).rstrip() + "\n"
