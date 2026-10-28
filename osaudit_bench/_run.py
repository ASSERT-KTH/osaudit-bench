"""Apply each mutant to a fresh copy of its base, audit it, and score the findings it adds.

A finding is new when the mutant's audit has it and the base's audit, with the same arguments,
does not: (file, rule, message), counted. A base need not be clean; what it already reports is
subtracted. A positive mutant is detected when a new finding has one of its expected rules in its
expected file, and located when the line agrees too. Any other new finding, outside the
mutant's collateral rules, is a false alarm.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._base import Base
from ._mutant import Mutant
from ._operators import mutants

IGNORE = shutil.ignore_patterns(".venv", "__pycache__", ".mypy_cache", ".pytest_cache",
                                ".ruff_cache")

Key = tuple[str, str, str]


class AuditError(Exception):
    pass


def audit(root: Path, check: tuple[str, ...]) -> list[dict[str, Any]]:
    p = subprocess.run([sys.executable, "-m", "osaudit", *check, "--root", str(root), "--json"],
                       cwd=root, capture_output=True, text=True)
    if p.returncode not in (0, 1):
        last = (p.stderr or p.stdout).strip().splitlines()[-1:]
        raise AuditError(last[0] if last else f"exit {p.returncode}")
    found: list[dict[str, Any]] = json.loads(p.stdout)
    return found


def key(f: dict[str, Any]) -> Key:
    return f["file"], f["rule"], f["message"]


@dataclass
class Outcome:
    base: str
    operator: str
    target: str
    check: list[str]
    expect: dict[str, Any] | None
    detected: bool | None = None      # None for a negative mutant
    located: bool | None = None
    false_alarms: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def score(m: Mutant, new: list[dict[str, Any]]) -> tuple[bool | None, bool | None,
                                                         list[dict[str, Any]]]:
    if m.expect is None:
        return None, None, new
    e = m.expect
    hits = [f for f in new if f["rule"] in e.rules and f["file"] == e.file]
    located = any(e.line is None or f["line"] == e.line for f in hits)
    alarms = [f for f in new if f not in hits and f["rule"] not in m.collateral]
    return bool(hits), bool(hits) and located, alarms


def run_mutant(base: Base, name: str, m: Mutant,
               baseline: dict[tuple[str, ...], Counter[Key]]) -> Outcome:
    out = Outcome(name, m.operator, m.target, list(m.check),
                  asdict(m.expect) if m.expect else None)
    try:
        if m.check not in baseline:
            baseline[m.check] = Counter(map(key, audit(base.root, m.check)))
        with tempfile.TemporaryDirectory(prefix="osaudit-bench-") as d:
            copy = Path(d) / "repo"
            shutil.copytree(base.root, copy, symlinks=True, ignore=IGNORE)
            m.edit(copy)
            found = audit(copy, m.check)
    except (AuditError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        out.error = f"{type(exc).__name__}: {exc}"
        return out
    left = baseline[m.check].copy()
    new = []
    for f in found:
        if left[key(f)] > 0:
            left[key(f)] -= 1
        else:
            new.append(f)
    out.detected, out.located, out.false_alarms = score(m, new)
    return out


def run(bases: Iterable[tuple[str, Path]], rerun: bool = False,
        only: set[str] | None = None) -> list[Outcome]:
    outcomes = []
    for name, root in bases:
        base = Base.load(root)
        baseline: dict[tuple[str, ...], Counter[Key]] = {}
        for m in mutants(base, rerun=rerun):
            if only is None or m.operator in only:
                outcomes.append(run_mutant(base, name, m, baseline))
    return outcomes


def summary(outcomes: list[Outcome]) -> dict[str, dict[str, int]]:
    """Per operator: mutants, detected, located, false alarms, errors."""
    out: dict[str, dict[str, int]] = {}
    for o in outcomes:
        s = out.setdefault(o.operator, {"mutants": 0, "detected": 0, "located": 0,
                                        "false_alarms": 0, "errors": 0})
        s["mutants"] += 1
        s["detected"] += bool(o.detected)
        s["located"] += bool(o.located)
        s["false_alarms"] += len(o.false_alarms)
        s["errors"] += o.error is not None
    return out
