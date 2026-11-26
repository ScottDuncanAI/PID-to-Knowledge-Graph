"""A small object model for DEXPI instances and its deterministic XML writer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

CORE_URI = "https://data.dexpi.org/models/2.0.1/Core.xml"
PLANT_URI = "https://data.dexpi.org/models/2.0.1/Plant.xml"


@dataclass(frozen=True)
class DateTime:
    value: str


@dataclass(frozen=True)
class EnumValue:
    ref: str


@dataclass(frozen=True)
class MultiLang:
    """Free text. The drawing language is not extracted, so it is written as Undefined."""

    value: str


Value = Union[str, DateTime, EnumValue, MultiLang]


class Obj:
    def __init__(self, type_: str, id_: Optional[str] = None):
        self.type = type_
        self.id = id_
        self.parent: Optional["Obj"] = None
        self.data: dict[str, Value] = {}
        self.components: dict[str, list["Obj"]] = {}
        self.references: dict[str, list["Obj"]] = {}

    def set(self, prop: str, value: Optional[Value]) -> "Obj":
        if value not in (None, ""):
            self.data[prop] = value
        return self

    def add(self, prop: str, child: "Obj") -> "Obj":
        child.parent = self
        self.components.setdefault(prop, []).append(child)
        return child

    def ref(self, prop: str, target: "Obj") -> "Obj":
        targets = self.references.setdefault(prop, [])
        if target not in targets:
            targets.append(target)
        return self


def _clean(text: str) -> str:
    return _ILLEGAL_XML.sub(" ", str(text))


def _write_value(etree, parent, value: Value) -> None:
    if isinstance(value, DateTime):
        etree.SubElement(parent, "DateTime").text = value.value
    elif isinstance(value, EnumValue):
        etree.SubElement(parent, "DataReference", data=value.ref)
    elif isinstance(value, MultiLang):
        multi = etree.SubElement(parent, "AggregatedDataValue", type="Core/DataTypes.MultiLanguageString")
        strings = etree.SubElement(multi, "Data", property="SingleLanguageStrings")
        single = etree.SubElement(strings, "AggregatedDataValue", type="Core/DataTypes.SingleLanguageString")
        etree.SubElement(etree.SubElement(single, "Data", property="Language"), "Undefined")
        etree.SubElement(etree.SubElement(single, "Data", property="Value"), "String").text = _clean(value.value)
    else:
        etree.SubElement(parent, "String").text = _clean(value)


def _write_object(etree, parent, obj: Obj) -> None:
    attrs = {"type": obj.type}
    if obj.id:
        attrs["id"] = obj.id
    el = etree.SubElement(parent, "Object", attrs)
    rows = [(p, 0) for p in obj.components] + [(p, 1) for p in obj.data] + [(p, 2) for p in obj.references]
    for prop, kind in sorted(rows):
        if kind == 0:
            node = etree.SubElement(el, "Components", property=prop)
            for child in obj.components[prop]:
                _write_object(etree, node, child)
        elif kind == 1:
            _write_value(etree, etree.SubElement(el, "Data", property=prop), obj.data[prop])
        else:
            objects = " ".join(f"#{t.id}" for t in obj.references[prop])
            etree.SubElement(el, "References", objects=objects, property=prop)


def to_xml(root_obj: Obj, model_name: str, model_uri: str, local_classes: dict[str, str]) -> bytes:
    from lxml import etree

    model = etree.Element("Model", name=model_name, uri=model_uri)
    etree.SubElement(model, "Import", prefix="Core", source=CORE_URI)
    etree.SubElement(model, "Import", prefix="Plant", source=PLANT_URI)
    for name, super_type in sorted(local_classes.items()):
        etree.SubElement(model, "ConcreteClass", name=name, superTypes=super_type)
    _write_object(etree, model, root_obj)
    etree.indent(model, space="  ")
    return etree.tostring(model, xml_declaration=True, encoding="UTF-8", pretty_print=True)
