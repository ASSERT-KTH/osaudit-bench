"""The `osaudit-bench` command line.

Exit status: 0 every positive mutant detected and no false alarm, 1 otherwise, 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import osaudit

from . import __version__, _run, _synth
from ._operators import OPERATORS

RULE = re.compile(r'"((?:numbers|quotes|results|study|schema)/[a-z-]+)"')


def osaudit_rules() -> set[str]:
    """Every rule osaudit can emit, read from its source."""
    src = Path(osaudit.__file__).parent
    return {m for f in src.glob("*.py") for m in RULE.findall(f.read_text())}


def uncovered() -> list[str]:
    return sorted(osaudit_rules() - {r for op in OPERATORS for r in op.rules})


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="osaudit-bench",
        description="Inject defects into paper repositories audited by osaudit, and report "
                    "per operator how many osaudit detected (recall) and the findings it "
                    "raised that no defect explains (false alarms). Silent when every defect "
                    "is detected and nothing else is reported.",
        epilog="exit status: 0 all detected and no false alarm, 1 otherwise, 2 bad usage")
    p.add_argument("--version", action="version", version=f"osaudit-bench {__version__}")
    p.add_argument("--base", type=Path, action="append", default=[],
                   help="a paper repository with an audit.toml (repeatable)")
    p.add_argument("--synth", action="store_true",
                   help="also run on the synthetic base (the default when no --base is given)")
    p.add_argument("--operator", action="append", help="run only this operator (repeatable)")
    p.add_argument("--rerun", action="store_true",
                   help="also run the operators that need `osaudit results --rerun` "
                        "(reruns each base's study once per mutant)")
    p.add_argument("--json", action="store_true",
                   help="print {summary, outcomes} as JSON")
    p.add_argument("--list-operators", action="store_true",
                   help="print each operator and the rules it targets, then exit")
    p.add_argument("--coverage", action="store_true",
                   help="print the osaudit rules no operator targets (silent when none)")
    return p


def report(outcomes: list[_run.Outcome]) -> list[str]:
    lines = []
    for op, s in sorted(_run.summary(outcomes).items()):
        if op.startswith("neg-"):
            lines.append(f"{op:28} {s['mutants']:3} mutants  {s['false_alarms']:3} false alarms"
                         f"  {s['errors']} errors")
        else:
            lines.append(f"{op:28} {s['detected']:3}/{s['mutants']:<3} detected"
                         f"  {s['located']:3} located  {s['false_alarms']:3} false alarms"
                         f"  {s['errors']} errors")
    for o in outcomes:
        where = f"{o.base}: {o.operator} {o.target}"
        if o.error:
            lines.append(f"error     {where}: {o.error}")
        if o.detected is False:
            lines.append(f"survivor  {where}: expected {o.expect}")
        for f in o.false_alarms:
            lines.append(f"alarm     {where}: {f['file']}:{f['line']}: {f['rule']}: "
                         f"{f['message']}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.list_operators:
        for op in OPERATORS:
            print(f"{op.name:28} {' '.join(op.rules) or '(negative)'}"
                  f"{'  [--rerun]' if op.rerun else ''}")
        return 0
    if args.coverage:
        missing = uncovered()
        for r in missing:
            print(f"uncovered: {r}")
        return 1 if missing else 0
    for b in args.base:
        if not (b / "audit.toml").is_file():
            print(f"osaudit-bench: {b}: no audit.toml", file=sys.stderr)
            return 2
    with tempfile.TemporaryDirectory(prefix="osaudit-bench-synth-") as d:
        bases = [(str(b), b.resolve()) for b in args.base]
        if args.synth or not bases:
            bases.append(("synth", _synth.build(Path(d) / "synth")))
        outcomes = _run.run(bases, rerun=args.rerun,
                            only=set(args.operator) if args.operator else None)
    bad = any(o.error or o.detected is False or o.false_alarms for o in outcomes)
    if args.json:
        print(json.dumps({"summary": _run.summary(outcomes),
                          "outcomes": [asdict(o) for o in outcomes]}, indent=1))
    elif bad:
        print("\n".join(report(outcomes)))
    return 1 if bad else 0
