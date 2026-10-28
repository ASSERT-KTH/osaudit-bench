"""The benchmark on its synthetic base, and the scorer against mutants of known outcome."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from functools import partial
from pathlib import Path

import pytest

from osaudit_bench._base import Base
from osaudit_bench._cli import main, uncovered
from osaudit_bench._mutant import Expect, Mutant
from osaudit_bench._operators import OPERATORS, appended, mutants
from osaudit_bench._run import Key, audit, run_mutant
from osaudit_bench._synth import build


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build(tmp_path_factory.mktemp("bench") / "synth")


@pytest.mark.parametrize("check", [("all",), ("results", "--inputs"), ("results", "--rerun")])
def test_synth_is_clean(synth: Path, check: tuple[str, ...]) -> None:  # the base: known-negative
    assert audit(synth, check) == []


def test_every_operator_has_a_mutant_on_synth(synth: Path) -> None:  # no vacuous operator
    made = Counter(m.operator for m in mutants(Base.load(synth), rerun=True))
    assert [op.name for op in OPERATORS if not made[op.name]] == []


def test_every_osaudit_rule_has_an_operator() -> None:
    assert uncovered() == []


def test_osaudit_detects_every_mutant_of_synth(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--rerun"]) == 0
    assert capsys.readouterr().out == ""  # silent when everything is detected


def one(synth: Path, m: Mutant) -> tuple[bool | None, list[str]]:
    o = run_mutant(Base.load(synth), "synth", m, {})
    assert o.error is None
    return o.detected, [f["rule"] for f in o.false_alarms]


def test_noop_edit_survives(synth: Path) -> None:  # scorer known-negative: nothing to detect
    m = Mutant("noop", "x", ("numbers",), lambda r: None,
               Expect(("numbers/untraced",), "paper.tex"))
    assert one(synth, m) == (False, [])


def test_defect_under_a_negative_is_an_alarm(synth: Path) -> None:  # scorer known-positive
    m = Mutant("neg-x", "x", ("numbers",),
               partial(appended, "paper.tex", "\nAn untraced 31337 here.\n"), None)
    assert one(synth, m) == (None, ["numbers/untraced"])


def test_wrong_file_is_not_detected(synth: Path) -> None:  # boundary: right rule, other file
    m = Mutant("x", "x", ("numbers",), partial(appended, "paper.tex", "\nUntraced 31337.\n"),
               Expect(("numbers/untraced",), "findings.md"))
    assert one(synth, m) == (False, ["numbers/untraced"])


def test_findings_of_the_base_are_subtracted(synth: Path) -> None:  # a base that is not clean
    baseline: dict[tuple[str, ...], Counter[Key]] = {
        ("numbers",): Counter({("paper.tex", "numbers/untraced",
                                "31337 matches no results value, derivation or allow entry"): 1})}
    m = Mutant("x", "x", ("numbers",), partial(appended, "paper.tex", "\nUntraced 31337.\n"),
               None)
    o = run_mutant(Base.load(synth), "synth", m, baseline)
    assert o.false_alarms == []


def test_cli_json(synth: Path) -> None:
    p = subprocess.run([sys.executable, "-m", "osaudit_bench", "--base", str(synth),
                        "--operator", "allow-orphan", "--json"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["summary"] == {"allow-orphan": {"mutants": 1, "detected": 1, "located": 1,
                                               "false_alarms": 0, "errors": 0}}
