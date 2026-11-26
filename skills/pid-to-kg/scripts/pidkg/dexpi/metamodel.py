"""Read the vendored DEXPI 2.0.1 model files into a queryable metamodel.

The XSD only checks the shape of a DEXPI file. Which classes exist, which
properties they have and how many values each may hold is defined by
``Core.xml`` and ``Plant.xml``. The exporter and validator both use this module
so they cannot disagree about the standard.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MODEL_DIR = Path(__file__).resolve().parents[3] / "references" / "dexpi" / "2.0.1"
XSD_PATH = MODEL_DIR / "DEXPI_XML_Schema.xsd"

MODEL_URIS = {
    "https://data.dexpi.org/models/2.0.1/Core.xml": "Core",
    "https://data.dexpi.org/models/2.0.1/Plant.xml": "Plant",
}
LOCAL = "$local"


@dataclass
class Prop:
    name: str
    kind: str  # composition | reference | data
    lower: int
    upper: Optional[int]  # None means unbounded
    types: list[str] = field(default_factory=list)


@dataclass
class ClassDef:
    qname: str
    abstract: bool
    supers: list[str]
    props: dict[str, Prop] = field(default_factory=dict)


def qualify(ref: str, model: str, prefixes: dict[str, str]) -> str:
    """Turn ``/Piping.Pipe`` or ``Core/X`` into a model-qualified name."""
    if ref.startswith("/"):
        return f"{model}{ref}"
    prefix, _, rest = ref.partition("/")
    return f"{prefixes.get(prefix, prefix)}/{rest}"


def _data_types(elem, model: str, prefixes: dict[str, str]) -> list[str]:
    out: list[str] = []
    for child in elem:
        if child.tag == "DataTypeReference":
            out.append(qualify(child.get("type"), model, prefixes))
        elif child.tag == "UnionDataType":
            out.extend(_data_types(child, model, prefixes))
        elif child.tag == "BoundDataType":
            ref = child.find("DataTypeReference")
            if ref is not None:
                out.append(qualify(ref.get("type"), model, prefixes))
    return out


def _props(elem, model: str, prefixes: dict[str, str]) -> dict[str, Prop]:
    props: dict[str, Prop] = {}
    kinds = {"CompositionProperty": "composition", "ReferenceProperty": "reference", "DataProperty": "data"}
    for child in elem:
        kind = kinds.get(child.tag)
        if not kind:
            continue
        upper = child.get("upper")
        if kind == "data":
            types = _data_types(child, model, prefixes)
        else:
            types = [qualify(c.get("type"), model, prefixes) for c in child.findall("ClassReference")]
        props[child.get("name")] = Prop(child.get("name"), kind, int(child.get("lower", "0")),
                                        int(upper) if upper is not None else None, types)
    return props


class Metamodel:
    def __init__(self, model_dir: Path = MODEL_DIR):
        self.classes: dict[str, ClassDef] = {}
        self.aggregates: dict[str, ClassDef] = {}
        self.enums: dict[str, set[str]] = {}
        self.data_supers: dict[str, list[str]] = {}
        self.parameters: set[str] = set()
        self._all_props: dict[str, dict[str, Prop]] = {}
        for name in ("Core", "Plant"):
            root = ET.parse(model_dir / f"{name}.xml").getroot()
            prefixes = {i.get("prefix"): MODEL_URIS.get(i.get("source"), i.get("prefix"))
                        for i in root.findall("Import")}
            self._walk(root, "", name, prefixes)
        self._by_simple: dict[str, list[str]] = {}
        for q in self.classes:
            self._by_simple.setdefault(q.rsplit("/", 1)[-1].rsplit(".", 1)[-1], []).append(q)

    def _walk(self, elem, path: str, model: str, prefixes: dict[str, str]) -> None:
        for c in elem:
            name = c.get("name")
            q = f"{model}/{path + '.' if path else ''}{name}"
            if c.tag == "Package":
                self._walk(c, f"{path}.{name}" if path else name, model, prefixes)
            elif c.tag in ("ConcreteClass", "AbstractClass"):
                self.add_class(q, c.tag == "AbstractClass",
                               [qualify(s, model, prefixes) for s in (c.get("superTypes") or "").split()],
                               _props(c, model, prefixes))
            elif c.tag == "AggregatedDataType":
                self.aggregates[q] = ClassDef(q, False, [], _props(c, model, prefixes))
                for p in c.findall("DataTypeParameter"):
                    self.parameters.add(f"{q}.{p.get('name')}")
            elif c.tag == "Enumeration":
                self.enums[q] = {l.get("name") for l in c.findall("EnumerationLiteral")}
                self.data_supers[q] = [qualify(s, model, prefixes) for s in (c.get("superTypes") or "").split()]
            elif c.tag == "AbstractDataType":
                self.data_supers[q] = [qualify(s, model, prefixes) for s in (c.get("superTypes") or "").split()]

    def add_class(self, qname: str, abstract: bool, supers: list[str], props: Optional[dict] = None) -> None:
        self.classes[qname] = ClassDef(qname, abstract, supers, props or {})
        self._all_props.clear()

    def resolve(self, simple: str) -> str:
        """The unique qualified Plant class for a simple class name."""
        hits = [q for q in self._by_simple.get(simple, []) if q.startswith("Plant/")]
        if len(hits) != 1:
            raise KeyError(f"DEXPI class '{simple}' does not resolve to exactly one Plant class")
        return hits[0]

    def ancestors(self, qname: str) -> set[str]:
        seen: set[str] = set()
        stack = [qname]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur in self.classes:
                stack.extend(self.classes[cur].supers)
        return seen

    def is_a(self, qname: str, target: str) -> bool:
        return target in self.ancestors(qname)

    def props(self, qname: str) -> dict[str, Prop]:
        if qname not in self._all_props:
            merged: dict[str, Prop] = {}
            for a in self.ancestors(qname):
                if a in self.classes:
                    merged.update(self.classes[a].props)
            self._all_props[qname] = merged
        return self._all_props[qname]

    def data_is_a(self, qname: str, target: str) -> bool:
        seen: set[str] = set()
        stack = [qname]
        while stack:
            cur = stack.pop()
            if cur == target:
                return True
            if cur not in seen:
                seen.add(cur)
                stack.extend(self.data_supers.get(cur, []))
        return False
