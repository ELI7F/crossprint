"""Parse/write Metadata/model_settings.config.

Real files look like:

    <?xml version="1.0" encoding="UTF-8"?>
    <config>
      <object id="26">
        <metadata key="name" value="R_HORN_1.stl"/>
        <metadata key="extruder" value="3"/>
        <part id="25" subtype="normal_part">
          <metadata key="name" value="R_HORN_1.stl"/>
          <metadata key="matrix" value="..."/>
          <metadata key="source_file" value="..."/>
          <mesh_stat edges_fixed="0" degenerate_facets="0" .../>
        </part>
      </object>
    </config>

The "extruder" metadata value is the logical color-slot index -- this is what
convert/color_mapping.py rewrites. Everything else (matrix, source_file,
mesh_stat, brim_type, enable_support, ...) is provenance/print-modifier data
that just needs to survive round-tripping.

Node is a generic lossless tree (tag + attrib + children) so any element or
attribute this module doesn't explicitly know about -- including ones added
by a future slicer version -- still round-trips instead of being silently
dropped. ObjectView/PartView are thin convenience accessors over that tree,
not a separate copy of the data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'


@dataclass
class Node:
    tag: str
    attrib: dict[str, str] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)

    @classmethod
    def from_element(cls, el: ET.Element) -> Node:
        return cls(tag=el.tag, attrib=dict(el.attrib), children=[Node.from_element(c) for c in el])

    def to_element(self) -> ET.Element:
        el = ET.Element(self.tag, self.attrib)
        for child in self.children:
            el.append(child.to_element())
        return el


class _MetadataOwner:
    """Shared get/set helpers for nodes whose children include <metadata key=.. value=../> entries."""

    node: Node

    def _metadata_children(self) -> list[Node]:
        return [c for c in self.node.children if c.tag == "metadata"]

    def get_metadata(self, key: str, default: str | None = None) -> str | None:
        for c in self._metadata_children():
            if c.attrib.get("key") == key:
                return c.attrib.get("value")
        return default

    def set_metadata(self, key: str, value: str) -> None:
        for c in self._metadata_children():
            if c.attrib.get("key") == key:
                c.attrib["value"] = value
                return
        # New keys go after the last existing metadata entry (before <part>/<mesh_stat> children).
        insert_at = len(self.node.children)
        for i, c in enumerate(self.node.children):
            if c.tag != "metadata":
                insert_at = i
                break
        self.node.children.insert(insert_at, Node(tag="metadata", attrib={"key": key, "value": value}))


class PartView(_MetadataOwner):
    def __init__(self, node: Node):
        self.node = node

    @property
    def id(self) -> str:
        return self.node.attrib.get("id", "")

    @property
    def subtype(self) -> str:
        return self.node.attrib.get("subtype", "")

    @property
    def name(self) -> str | None:
        return self.get_metadata("name")


class ObjectView(_MetadataOwner):
    def __init__(self, node: Node):
        self.node = node

    @property
    def id(self) -> str:
        return self.node.attrib.get("id", "")

    @property
    def name(self) -> str | None:
        return self.get_metadata("name")

    @property
    def extruder(self) -> str | None:
        """Logical color-slot index for the whole object, if set at object level."""
        return self.get_metadata("extruder")

    @extruder.setter
    def extruder(self, value: str) -> None:
        self.set_metadata("extruder", str(value))

    @property
    def parts(self) -> list[PartView]:
        return [PartView(c) for c in self.node.children if c.tag == "part"]

    def part_extruders(self) -> dict[str, str | None]:
        """Per-part extruder overrides (parts don't usually carry their own 'extruder'
        metadata in the samples seen, but nothing in the format rules it out)."""
        return {p.id: p.get_metadata("extruder") for p in self.parts}


@dataclass
class ModelSettings:
    root: Node

    @classmethod
    def parse(cls, xml_text: str) -> ModelSettings:
        return cls(root=Node.from_element(ET.fromstring(xml_text)))

    @property
    def objects(self) -> list[ObjectView]:
        return [ObjectView(c) for c in self.root.children if c.tag == "object"]

    def object_by_id(self, obj_id: str) -> ObjectView | None:
        for o in self.objects:
            if o.id == obj_id:
                return o
        return None

    def all_extruder_slots(self) -> set[str]:
        """Every logical color slot referenced anywhere in the file (object- and part-level)."""
        slots: set[str] = set()
        for obj in self.objects:
            if obj.extruder is not None:
                slots.add(obj.extruder)
            for part in obj.parts:
                v = part.get_metadata("extruder")
                if v is not None:
                    slots.add(v)
        return slots

    def to_xml(self) -> str:
        el = self.root.to_element()
        ET.indent(el, space="  ")
        body = ET.tostring(el, encoding="unicode")
        return XML_DECLARATION + body + "\n"
