"""CLI: python cli.py convert input.3mf --to h2d [-o output.3mf]
       python cli.py convert input.3mf --to h2d --report      (what changed)
       python cli.py convert input.3mf --to h2d --dry-run --json
       python cli.py models                  (list supported --to values)

Source vendor is auto-detected from the file's own printer_model rather than
taken as a flag -- one less argument that can silently disagree with the
file, and convert.pipeline.detect_vendor already gives a clear error if the
file isn't a supported source.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from convert.pipeline import MODEL_REGISTRY, _WELL_VERIFIED_MODELS, convert


def _survive_a_narrow_console() -> None:
    """Never let an unencodable character truncate the output.

    Preset and filament names come out of user files and are routinely not
    ASCII, while a Windows console defaults to a legacy code page that can't
    encode them. Printing one raises UnicodeEncodeError *mid-report*, so the
    user sees a partial change list and a traceback instead of the answer.
    Degrading the character is strictly better than losing the report.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):  # not a reconfigurable text stream (a pytest capture, say)
            pass


def main(argv: list[str] | None = None) -> int:
    _survive_a_narrow_console()
    parser = argparse.ArgumentParser(prog="3mf-bridge", description="Convert .3mf projects between Snapmaker U1 and Bambu Lab printers")
    sub = parser.add_subparsers(dest="command", required=True)

    p_convert = sub.add_parser("convert", help="convert a .3mf to a different target printer")
    p_convert.add_argument("input", type=Path, help="source .3mf file (must be a model this tool recognizes -- see the 'models' command)")
    p_convert.add_argument("--to", required=True, choices=sorted(MODEL_REGISTRY), metavar="TARGET", help="target printer slug (see the 'models' command)")
    p_convert.add_argument("-o", "--output", type=Path, default=None, help="output .3mf path (default: <input stem>.<to>.3mf next to the input)")
    p_convert.add_argument("--report", action="store_true", help="print every change conversion made, not just the warnings")
    p_convert.add_argument("--json", action="store_true", help="print the full change report as JSON on stdout (implies --report)")
    p_convert.add_argument("--dry-run", action="store_true", help="convert and report, but write no output file")

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

    try:
        if not args.dry_run:
            archive.write(output)
    finally:
        archive.close()  # the source container stays open until the copy is done

    if args.json:
        # Stdout carries only the JSON so it can be piped; the human summary
        # below goes to stderr in that mode rather than corrupting it.
        json.dump(
            {
                "source": result.source_vendor,
                "target": result.target_vendor,
                "filamentCount": result.filament_count,
                "output": None if args.dry_run else str(output),
                "warnings": result.warnings,
                "changes": result.report.to_json(),
            },
            sys.stdout,
            indent=2,
        )
        print()

    out = sys.stderr if args.json else sys.stdout
    print(f"{args.input.name}: {result.source_vendor} -> {result.target_vendor}, {result.filament_count} color(s)", file=out)
    if args.report and not args.json:
        for line in result.report.text_lines():
            print(line)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.dry_run:
        print(f"dry run -- would have written {output}", file=out)
    else:
        print(f"wrote {output}", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
