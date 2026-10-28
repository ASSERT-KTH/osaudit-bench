"""A base: a paper repository, read independently of osaudit, and the edits operators make to it.

What osaudit would audit is read from `audit.toml` with tomllib and globs, not with osaudit's own
loader, so that a bug in osaudit's reading cannot hide the targets it should check.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

COMMIT_DATE = "2026-06-01T00:00:00+00:00"  # every mutant commit: after any base's own history


@dataclass
class Base:
    root: Path
    audit: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> Base:
        return cls(root, tomllib.loads((root / "audit.toml").read_text()))

    def glob(self, patterns: list[str]) -> list[Path]:
        return sorted({p for g in patterns for p in self.root.glob(g) if p.is_file()})

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    @property
    def tex(self) -> list[Path]:
        return self.glob(self.audit.get("paper", {}).get("tex", []))

    @property
    def md(self) -> list[Path]:
        return [p for p in self.glob(self.audit.get("paper", {}).get("md", []))
                if p.read_text().startswith("+++")]

    @property
    def results(self) -> list[Path]:
        r = self.audit.get("results", {})
        study = self.audit.get("study", {}).get("results", "results")
        return self.glob(r.get("files", [f"{study}/*.json"]))

    @property
    def instruments(self) -> list[Path]:
        return self.glob(self.audit.get("results", {}).get("instruments", []))

    @property
    def quotes(self) -> list[dict[str, Any]]:
        q: list[dict[str, Any]] = self.audit.get("quotes", [])
        return q

    @property
    def is_git(self) -> bool:
        return (self.root / ".git").exists()

    def study_dir(self, kind: str) -> Path:
        return self.root / str(self.audit.get("study", {}).get(kind, kind))

    def provenanced(self, mode: str | None = "any") -> list[tuple[Path, dict[str, Any]]]:
        """Results files with a provenance block (of that mode; "any" for every one)."""
        out = []
        for f in self.results:
            try:
                doc = json.loads(f.read_text())
            except (ValueError, UnicodeDecodeError):
                continue
            prov = doc.get("provenance") if isinstance(doc, dict) else None
            if isinstance(prov, dict) and (mode == "any" or prov.get("mode") == mode):
                out.append((f, prov))
        return out


def git(root: Path, *args: str, date: str = COMMIT_DATE) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=bench",
                           "-c", "user.email=bench@bench", "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True, env=env).stdout.strip()


def commit(root: Path, date: str = COMMIT_DATE) -> None:
    git(root, "add", "-A", date=date)
    git(root, "commit", "-q", "--allow-empty", "--no-verify", "-m", "mutant", date=date)


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def replace_at(path: Path, start: int, end: int, new: str) -> None:
    text = path.read_text()
    path.write_text(text[:start] + new + text[end:])


def toml_entry_line(root: Path, table: str, index: int) -> int:
    """The line of the `index`-th `[[table]]` header of audit.toml."""
    heads = [m.start() for m in re.finditer(rf"^\[\[{re.escape(table)}\]\]", (
        root / "audit.toml").read_text(), re.M)]
    return line_of((root / "audit.toml").read_text(), heads[index])


def toml_entry_span(root: Path, table: str, index: int) -> tuple[int, int]:
    """(start, end) offsets of the `index`-th `[[table]]` entry of audit.toml, header included."""
    text = (root / "audit.toml").read_text()
    starts = [m.start() for m in re.finditer(r"^\[", text, re.M)]
    heads = [m.start() for m in re.finditer(rf"^\[\[{re.escape(table)}\]\]", text, re.M)]
    s = heads[index]
    later = [t for t in starts if t > s]
    return s, later[0] if later else len(text)


def toml_str(s: str) -> str:
    return json.dumps(s)  # a JSON string is a valid TOML basic string
