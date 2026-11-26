"""DEXPI 2.0.1 native XML export. ``export_dexpi`` is the single entry point."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

XML_NAME = "graph.dexpi.xml"
REPORT_NAME = "dexpi_export_report.md"


@dataclass
class DexpiResult:
    status: str = "failed"  # passed | passed_with_warnings | failed
    xml_path: Optional[Path] = None
    report_path: Optional[Path] = None
    xsd_status: str = "not run"
    model_status: str = "not run"
    objects: int = 0
    qa_errors: int = 0
    graph_sha256: Optional[str] = None
    xml_sha256: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    mapping: Any = None

    @property
    def ok(self) -> bool:
        return self.status != "failed"

    @property
    def warning_count(self) -> int:
        return sum(len(v) for v in self.mapping.warnings.values()) if self.mapping else 0

    def summary_line(self) -> str:
        if not self.ok:
            first = self.errors[0] if self.errors else "unknown error"
            return f"dexpi: FAILED, no graph.dexpi.xml written ({first}). See {REPORT_NAME}"
        if self.status == "passed_with_warnings":
            return (f"dexpi: valid DEXPI 2.0.1, {self.objects} objects -> {XML_NAME} "
                    f"({self.warning_count} mapping warnings, see {REPORT_NAME})")
        return f"dexpi: valid DEXPI 2.0.1, {self.objects} objects -> {XML_NAME}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_dexpi(kg, out_dir: Path) -> DexpiResult:
    """Map, validate and publish the DEXPI file. Failures are reported, never raised.

    Any previous XML is removed first and the new file only takes the final
    name after validation, so ``graph.dexpi.xml`` always belongs to this build.
    """
    out_dir = Path(out_dir)
    xml_path, report_path = out_dir / XML_NAME, out_dir / REPORT_NAME
    tmp_path = out_dir / f"{XML_NAME}.tmp"
    result = DexpiResult(report_path=report_path)
    result.qa_errors = sum(1 for i in kg.issues if i.get("severity") == "error")
    graph_json = out_dir / "graph.json"
    try:
        if graph_json.exists():
            result.graph_sha256 = _sha256(graph_json.read_bytes())
        xml_path.unlink(missing_ok=True)

        from .mapping import map_graph
        from .metamodel import Metamodel
        from .validate import validate_bytes
        from .writer import to_xml

        result.mapping = map_graph(kg, Metamodel())
        m = result.mapping
        xml = to_xml(m.root, m.model_name, m.model_uri, m.local_classes)
        check = validate_bytes(xml)
        result.objects = check.objects
        result.xsd_status = "passed" if not check.xsd_errors else f"FAILED ({len(check.xsd_errors)} errors)"
        result.model_status = "passed" if not check.model_errors else f"FAILED ({len(check.model_errors)} errors)"
        if not check.ok:
            result.errors = check.xsd_errors + check.model_errors
        else:
            tmp_path.write_bytes(xml)
            os.replace(tmp_path, xml_path)
            result.xml_path = xml_path
            result.xml_sha256 = _sha256(xml)
            result.status = "passed_with_warnings" if m.warnings else "passed"
    except Exception as e:  # the other graph outputs must survive any exporter failure
        result.status = "failed"
        result.xml_path = None
        result.xml_sha256 = None
        result.errors.append(f"{type(e).__name__}: {e}")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    from .report import render_report

    try:
        report_path.write_text(render_report(result), encoding="utf-8")
    except Exception as e:
        result.errors.append(f"could not write {REPORT_NAME}: {e}")
        if result.ok:
            result.status = "failed"
            result.xml_path = None
            xml_path.unlink(missing_ok=True)
    return result
