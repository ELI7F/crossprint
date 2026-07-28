"""CLI: python cli.py convert input.3mf --to h2d [-o output.3mf]
       python cli.py models                  (list supported --to values)

Source vendor is auto-detected from the file's own printer_model rather than
taken as a flag -- one less argument that can silently disagree with the
file, and convert.pipeline.detect_vendor already gives a clear error if the
file isn't a supported source.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from convert.pipeline import MODEL_REGISTRY, _WELL_VERIFIED_MODELS, convert


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="3mf-bridge", description="Convert .3mf projects between Snapmaker U1 and Bambu Lab printers")
    sub = parser.add_subparsers(dest="command", required=True)

    p_convert = sub.add_parser("convert", help="convert a .3mf to a different target printer")
    p_convert.add_argument("input", type=Path, help="source .3mf file (must be a model this tool recognizes -- see the 'models' command)")
    p_convert.add_argument("--to", required=True, choices=sorted(MODEL_REGISTRY), metavar="TARGET", help="target printer slug (see the 'models' command)")
    p_convert.add_argument("-o", "--output", type=Path, default=None, help="output .3mf path (default: <input stem>.<to>.3mf next to the input)")

    sub.add_parser("models", help="list supported printer slugs")

    args = parser.parse_args(argv)

    if args.command == "models":
        for slug in sorted(MODEL_REGISTRY):
            tag = "" if slug in _WELL_VERIFIED_MODELS else "  (best-effort, not independently verified)"
            print(f"{slug:10} {MODEL_REGISTRY[slug]}{tag}")
        return 0

    if not args.input.exists():
        print(f"error: {args.input} not found", file=sys.stderr)
        return 1

    output = args.output or args.input.with_name(f"{args.input.stem}.{args.to}.3mf")

    try:
        archive, result = convert(args.input, args.to)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    archive.write(output)

    print(f"{args.input.name}: {result.source_vendor} -> {result.target_vendor}, {result.filament_count} color(s)")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
