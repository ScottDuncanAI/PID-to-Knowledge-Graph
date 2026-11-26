"""Two-layer validation of a DEXPI 2.0.1 XML document.

1. The official XSD checks document structure.
2. The metamodel check uses ``Core.xml`` and ``Plant.xml`` to verify classes,
   properties, multiplicities, value types, enumeration literals and references.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from .metamodel import LOCAL, MODEL_URIS, XSD_PATH, Metamodel, qualify

_VALUE_TYPES = {
    "String": {"Builtin/String", "Builtin/AnyURI"},
    "Double": {"Builtin/Double"},
    "Integer": {"Builtin/Integer", "Builtin/UnsignedByte"},
    "Boolean": {"Builtin/Boolean"},
    "DateTime": {"Builtin/DateTime"},
    "Undefined": {"Builtin/Undefined"},
}
_MAX_ERRORS = 200


@dataclass
class ValidationResult:
    xsd_errors: list[str] = field(default_factory=list)
    model_errors: list[str] = field(default_factory=list)
    objects: int = 0

    @property
    def ok(self) -> bool:
        return not self.xsd_errors and not self.model_errors


def _elements(node):
    return [c for c in node if isinstance(c.tag, str)]


class _Checker:
    def __init__(self, root, mm: Metamodel):
        self.mm = mm
        self.errors: list[str] = []
        self.prefixes: dict[str, str] = {}
        for imp in root.findall("Import"):
            model = MODEL_URIS.get(imp.get("source"))
            if model is None:
                self.err(f"import '{imp.get('prefix')}' has unknown source {imp.get('source')}")
            else:
                self.prefixes[imp.get("prefix")] = model
        for c in _elements(root):
            if c.tag in ("ConcreteClass", "AbstractClass"):
                supers = [qualify(s, LOCAL, self.prefixes) for s in (c.get("superTypes") or "").split()]
                for s in supers:
                    if s not in mm.classes:
                        self.err(f"local class {c.get('name')}: unknown supertype {s}")
                mm.add_class(f"{LOCAL}/{c.get('name')}", c.tag == "AbstractClass", supers)
        self.objects = list(root.iter("Object"))
        self.by_id: dict[str, object] = {}
        for o in self.objects:
            oid = o.get("id")
            if oid is None:
                continue
            if oid in self.by_id:
                self.err(f"duplicate object id '{oid}'")
            self.by_id[oid] = o

    def err(self, message: str) -> None:
        if len(self.errors) < _MAX_ERRORS:
            self.errors.append(message)

    def q(self, ref: str) -> str:
        return qualify(ref, LOCAL, self.prefixes)

    def where(self, obj) -> str:
        return f"{obj.get('type')}#{obj.get('id') or '?'} (line {obj.sourceline})"

    def run(self) -> None:
        for o in self.objects:
            self.check_object(o)

    def check_object(self, obj) -> None:
        q = self.q(obj.get("type"))
        cls = self.mm.classes.get(q)
        if cls is None:
            self.err(f"{self.where(obj)}: class does not exist")
            return
        if cls.abstract:
            self.err(f"{self.where(obj)}: class is abstract")
        props = self.mm.props(q)
        counts: dict[str, int] = {}
        for child in _elements(obj):
            name = child.get("property")
            prop = props.get(name)
            if prop is None:
                self.err(f"{self.where(obj)}: class has no property '{name}'")
                continue
            expected = {"Components": "composition", "References": "reference", "Data": "data"}[child.tag]
            if prop.kind != expected:
                self.err(f"{self.where(obj)}: '{name}' is a {prop.kind} property, written as {child.tag}")
                continue
            if child.tag == "Components":
                counts[name] = counts.get(name, 0) + self.check_components(obj, child, prop)
            elif child.tag == "References":
                counts[name] = counts.get(name, 0) + self.check_references(obj, child, prop)
            else:
                values = _elements(child)
                counts[name] = counts.get(name, 0) + len(values)
                for v in values:
                    self.check_value(v, prop, self.where(obj))
        self.check_counts(props, counts, self.where(obj))

    def check_counts(self, props, counts, where: str) -> None:
        for name, prop in props.items():
            n = counts.get(name, 0)
            if n < prop.lower:
                self.err(f"{where}: '{name}' needs at least {prop.lower} value(s), found {n}")
            if prop.upper is not None and n > prop.upper:
                self.err(f"{where}: '{name}' allows at most {prop.upper} value(s), found {n}")

    def check_target(self, obj, name: str, target, prop) -> None:
        tq = self.q(target.get("type"))
        if not any(self.mm.is_a(tq, t) for t in prop.types):
            self.err(f"{self.where(obj)}: '{name}' cannot hold a {target.get('type')}")

    def check_components(self, obj, node, prop) -> int:
        children = _elements(node)
        for c in children:
            if c.tag == "Object":
                self.check_target(obj, prop.name, c, prop)
            else:
                self.check_id_ref(obj, prop, c.get("object"))
        return len(children)

    def check_id_ref(self, obj, prop, ref: str) -> None:
        target = self.by_id.get((ref or "").lstrip("#"))
        if target is None:
            self.err(f"{self.where(obj)}: '{prop.name}' points to missing object '{ref}'")
        else:
            self.check_target(obj, prop.name, target, prop)

    def check_references(self, obj, node, prop) -> int:
        refs = node.get("objects").split()
        for ref in refs:
            self.check_id_ref(obj, prop, ref)
        return len(refs)

    def check_value(self, value, prop, where: str) -> None:
        allowed = prop.types
        if any(t in self.mm.parameters for t in allowed):
            return  # generic parameter, bound by the enclosing physical quantity
        if value.tag in _VALUE_TYPES:
            if not _VALUE_TYPES[value.tag] & set(allowed):
                self.err(f"{where}: '{prop.name}' cannot hold a {value.tag} value")
        elif value.tag == "DataReference":
            enum, _, literal = self.q(value.get("data")).rpartition(".")
            if enum not in self.mm.enums or literal not in self.mm.enums[enum]:
                self.err(f"{where}: '{prop.name}' has unknown enumeration value {value.get('data')}")
            elif not any(self.mm.data_is_a(enum, t) for t in allowed):
                self.err(f"{where}: '{prop.name}' cannot hold {value.get('data')}")
        elif value.tag == "AggregatedDataValue":
            q = self.q(value.get("type"))
            agg = self.mm.aggregates.get(q)
            if agg is None or q not in allowed:
                self.err(f"{where}: '{prop.name}' cannot hold aggregated type {value.get('type')}")
                return
            counts: dict[str, int] = {}
            for d in _elements(value):
                sub = agg.props.get(d.get("property"))
                if sub is None:
                    self.err(f"{where}: {value.get('type')} has no field '{d.get('property')}'")
                    continue
                values = _elements(d)
                counts[sub.name] = counts.get(sub.name, 0) + len(values)
                for v in values:
                    self.check_value(v, sub, where)
            self.check_counts(agg.props, counts, f"{where} {value.get('type')}")


def validate_bytes(xml: bytes, mm: Metamodel | None = None) -> ValidationResult:
    result = ValidationResult()
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError as e:
        result.xsd_errors.append(f"not well-formed XML: {e}")
        return result
    schema = etree.XMLSchema(etree.parse(str(XSD_PATH)))
    if not schema.validate(root):
        result.xsd_errors = [f"line {e.line}: {e.message}" for e in list(schema.error_log)[:_MAX_ERRORS]]
    # A fresh metamodel per document keeps one file's local classes out of the next.
    checker = _Checker(root, Metamodel() if mm is None else mm)
    checker.run()
    result.model_errors = checker.errors
    result.objects = len(checker.objects)
    return result
