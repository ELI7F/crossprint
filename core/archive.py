"""Read/write the .3mf OPC zip container.

Every part is kept as opaque bytes here. Higher layers (model_settings,
project_settings) parse specific named parts on demand; everything else
(thumbnails, gcode previews, cut_information.xml, filament_sequence.json,
numbered *_settings_N.config snapshots) passes through untouched unless a
caller explicitly removes or replaces it.

Parts are read **lazily** and untouched ones are never decompressed at all.
That is not a micro-optimisation -- it is what decides whether a real project
can be converted on a small container. Measured on an 11-plate project: 726 MB
of the 727 MB uncompressed archive is geometry, spread over per-object
`3D/Objects/object_N.model` parts that conversion never modifies. Only
`3D/3dmodel.model` (small -- it holds the build transforms, not the meshes)
and the ~1 MB of config parts are rewritten. Eagerly materialising all of it
cost about a gigabyte to change a few kilobytes.

So the source archive is held open and each part is fetched on demand, while
`write` copies any part the caller never replaced straight from the source zip
to the destination in fixed-size chunks. Peak memory becomes a function of the
largest *single* part touched, not of the project's total size.

The cost is a live file handle for the archive's lifetime: use it as a context
manager, or call `close()`, once the output has been written. Archives built
in memory (no source) have nothing to close and ignore both.

[Content_Types].xml in real Bambu Studio / Orca output only declares Default
entries for rels/model/png/gcode -- .config and .json parts aren't declared
at all. Both slicers accept that, so this module never needs to touch
Content_Types.xml for the file types this tool adds or removes.
"""
from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

CONTENT_TYPES = "[Content_Types].xml"

# zipfile.ZipFile happily takes a path or a file-like object; widening the
# hints below (rather than only accepting str | Path) lets the web UI hand
# it an in-memory BytesIO for an uploaded file, with no on-disk temp file.
PathOrStream = str | Path | BinaryIO

_COPY_CHUNK = 1 << 20  # 1 MiB, the streaming copy's memory cost per part

# Parts at or below this size are cached once read. The config parts are small
# and read repeatedly (project_settings alone is parsed by several stages);
# geometry is large and read at most once, so caching it would give back
# exactly the memory this module exists to save.
_CACHE_MAX_BYTES = 4 << 20


@dataclass
class ThreeMFArchive:
    #: Parts held in memory: everything for an in-memory archive, and for one
    #: opened from a file, only those replaced by a caller plus small cached
    #: reads. Never assume a part is here -- go through `get_bytes`.
    parts: dict[str, bytes] = field(default_factory=dict)
    part_order: list[str] = field(default_factory=list)
    # Per-part zip compression method, carried from the source container so a
    # rewritten .3mf stays as close to what the original slicer produced as
    # possible. Real Bambu Studio output stores the PNG thumbnails uncompressed
    # (ZIP_STORED) and deflates everything else; re-deflating the PNGs would
    # work but needlessly makes the container differ from every reference file.
    compression: dict[str, int] = field(default_factory=dict)

    _source: zipfile.ZipFile | None = field(default=None, repr=False)
    #: Timestamps from the source, so a passed-through part keeps its own.
    _timestamps: dict[str, tuple] = field(default_factory=dict, repr=False)

    @classmethod
    def open(cls, path: PathOrStream) -> ThreeMFArchive:
        """Open a container without reading any part data.

        The returned archive holds `path` open. Close it (or use it as a
        context manager) once you're done -- on Windows especially, an open
        handle keeps the source file locked.
        """
        source = zipfile.ZipFile(path, "r")
        order: list[str] = []
        compression: dict[str, int] = {}
        timestamps: dict[str, tuple] = {}
        for info in source.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            order.append(name)
            compression[name] = info.compress_type
            timestamps[name] = info.date_time
        return cls(part_order=order, compression=compression, _source=source, _timestamps=timestamps)

    def __enter__(self) -> ThreeMFArchive:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        """Release the source container. Reading a not-yet-loaded part after
        this raises; parts already in memory stay available."""
        if self._source is not None:
            self._source.close()
            self._source = None

    def _source_name(self, name: str) -> str:
        """Names are normalised to forward slashes on open; a container that
        used backslashes needs the original spelling to be looked up again."""
        if self._source is None:
            raise ValueError(f"archive is closed; {name!r} was never loaded")
        try:
            self._source.getinfo(name)
        except KeyError:
            return name.replace("/", "\\")
        return name

    def _read_source(self, name: str) -> bytes:
        # Resolve the name first: it is what raises on a closed archive, and
        # `self._source.read(...)` would otherwise fail on the attribute lookup
        # with a bare AttributeError before the argument is ever evaluated.
        source_name = self._source_name(name)
        return self._source.read(source_name)  # type: ignore[union-attr]

    def get_bytes(self, name: str) -> bytes | None:
        if name in self.parts:
            return self.parts[name]
        if name not in self.part_order:
            return None
        data = self._read_source(name)
        if len(data) <= _CACHE_MAX_BYTES:
            self.parts[name] = data
        return data

    def get_text(self, name: str, encoding: str = "utf-8") -> str | None:
        data = self.get_bytes(name)
        return None if data is None else data.decode(encoding)

    def open_part(self, name: str) -> BinaryIO:
        """A readable stream over a part, for callers that can work in chunks
        instead of materialising a whole mesh. Close it when done."""
        if name in self.parts:
            return BytesIO(self.parts[name])
        if name not in self.part_order:
            raise KeyError(name)
        return self._source.open(self._source_name(name))  # type: ignore[union-attr,return-value]

    def set_bytes(self, name: str, data: bytes) -> None:
        if name not in self.part_order:
            self.part_order.append(name)
        self.parts[name] = data

    def set_text(self, name: str, text: str, encoding: str = "utf-8") -> None:
        self.set_bytes(name, text.encode(encoding))

    def remove(self, name: str) -> None:
        if name in self.part_order:
            self.part_order.remove(name)
            self.parts.pop(name, None)
            self.compression.pop(name, None)

    def has(self, name: str) -> bool:
        return name in self.part_order

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
                compress_type = self.compression.get(name, zipfile.ZIP_DEFLATED)
                data = self.parts.get(name)
                if data is not None:
                    zf.writestr(name, data, compress_type=compress_type)
                else:
                    self._copy_through(zf, name, compress_type)

    def _copy_through(self, dest: zipfile.ZipFile, name: str, compress_type: int) -> None:
        """Stream a part the caller never touched from source to destination.

        It is still decompressed and recompressed -- zipfile offers no
        supported way to move a member's raw bytes -- but only `_COPY_CHUNK`
        of it exists at a time, which is the point.
        """
        info = zipfile.ZipInfo(name, date_time=self._timestamps.get(name, (1980, 1, 1, 0, 0, 0)))
        info.compress_type = compress_type
        source_name = self._source_name(name)  # raises if the archive was closed
        with self._source.open(source_name) as src, dest.open(info, "w") as out:  # type: ignore[union-attr]
            shutil.copyfileobj(src, out, _COPY_CHUNK)
