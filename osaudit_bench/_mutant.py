"""A mutant: one defect (or one meaning-preserving edit) injected into a copy of a base."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Expect:
    """The finding a mutant must cause: one of its rules, in its file, at its line when known."""

    rules: tuple[str, ...]
    file: str
    line: int | None = None


@dataclass(frozen=True)
class Mutant:
    operator: str
    target: str                       # what was mutated, for the report
    check: tuple[str, ...]            # the osaudit arguments that should see it
    edit: Callable[[Path], None]      # applied to the copy's root
    expect: Expect | None             # None: a negative mutant, any new finding is a false alarm
    collateral: frozenset[str] = field(default_factory=frozenset)  # rules that legitimately
                                                                   # follow from the defect
