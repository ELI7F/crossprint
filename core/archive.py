"""Read/write the .3mf OPC zip container.

Every part is kept as opaque bytes here. Higher layers (model_settings,
project_settings) parse specific named parts on demand; everything else
(thumbnails, gcode previews, cut_information.xml, filament_sequence.json,
numbered *_settings_N.config snapshots) passes through untouched unless a
caller explicitly removes or replaces it.

[Content_Types].xml in real Bambu Studio / Orca output only declares Default
entries for rels/model/png/gcode -- .config and .json parts aren't declared
at all. Both slicers accept that, so this module never needs to touch
Content_Types.xml for the file types this tool adds or removes.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

CONTENT_TYPES = "[Content_Types].xml"

# zipfile.ZipFile happily takes a path or a file-like object; widening the
# hints below (rather than only accepting str | Path) lets the web UI hand
# it an in-memory BytesIO for an uploaded file, with no on-disk temp file.
PathOrStream = str | Path | BinaryIO


@dataclass
class ThreeMFArchive:
    parts: dict[str, bytes] = field(default_factory=dict)
    part_order: list[str] = field(default_factory=list)
    # Per-part zip compression method, carried from the source container so a
    # rewritten .3mf stays as close to what the original slicer produced as
    # possible. Real Bambu Studio output stores the PNG thumbnails uncompressed
    # (ZIP_STORED) and deflates everything else; re-deflating the PNGs would
    # work but needlessly makes the container differ from every reference file.
    compression: dict[str, int] = field(default_factory=dict)

    @classmethod
    def open(cls, path: PathOrStream) -> ThreeMFArchive:
        parts: dict[str, bytes] = {}
        order: list[str] = []
        compression: dict[str, int] = {}
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                parts[name] = zf.read(info)
                order.append(name)
                compression[name] = info.compress_type
        return cls(parts=parts, part_order=order, compression=compression)

    def get_bytes(self, name: str) -> bytes | None:
        return self.parts.get(name)

    def get_text(self, name: str, encoding: str = "utf-8") -> str | None:
        data = self.parts.get(name)
        return None if data is None else data.decode(encoding)

    def set_bytes(self, name: str, data: bytes) -> None:
        if name not in self.parts:
            self.part_order.append(name)
        self.parts[name] = data

    def set_text(self, name: str, text: str, encoding: str = "utf-8") -> None:
        self.set_bytes(name, text.encode(encoding))

    def remove(self, name: str) -> None:
        if name in self.parts:
            del self.parts[name]
            self.part_order.remove(name)
            self.compression.pop(name, None)

    def has(self, name: str) -> bool:
        return name in self.parts

    def names(self) -> list[str]:
        return list(self.part_order)

    def names_matching(self, prefix: str) -> list[str]:
        return [n for n in self.part_order if n.startswith(prefix)]

    def write(self, path: PathOrStream) -> None:
        # [Content_Types].xml first, matching real-world OPC convention.
        ordered = [n for n in self.part_order if n == CONTENT_TYPES]
        ordered += [n for n in self.part_order if n != CONTENT_TYPES]
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in ordered:
                zf.writestr(name, self.parts[name], compress_type=self.compression.get(name, zipfile.ZIP_DEFLATED))
