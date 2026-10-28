"""Mutation operators: each finds its targets in a base and yields mutants, one defect each.

A positive operator injects a defect that one osaudit rule exists to catch; a negative operator
makes an edit that keeps every claim traced, so any finding it causes is a false alarm. Targets
are found with this module's own regular expressions, independent of osaudit's extraction.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from pathlib import Path
from typing import Any

from ._base import (
    Base,
    commit,
    head,
    line_of,
    replace_at,
    toml_entry_line,
    toml_entry_span,
    toml_str,
)
from ._mutant import Expect, Mutant

SCHEMAS = "https://github.com/ASSERT-KTH/assert-research-agent/blob/main/schemas/"
ALL = ("all",)

# A number as printed in prose: after a space or "(", before a space or punctuation that no
# digit follows (not a coordinate such as `(90:2.9)`).
NUMBER = re.compile(
    r"(?<=[\s(])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\\%|%)?(?=[\s).,;:](?!\d)|$)")
QUOTE = re.compile(r"``(.+?)''")
CITE = re.compile(r"\\cite[tp]?\*?((?:\[[^\]]*\])*)\{([^}]+)\}")
PAGE = re.compile(r"(pp?\.~?)(\d+)")


@dataclass(frozen=True)
class Operator:
    name: str
    rules: tuple[str, ...]            # () for a negative operator
    generate: Callable[[Base], Iterator[Mutant]]
    rerun: bool = False               # needs `osaudit results --rerun`: opt-in, it runs the study


OPERATORS: list[Operator] = []


def operator(name: str, *rules: str, rerun: bool = False) -> Callable[
        [Callable[[Base], Iterator[Mutant]]], Callable[[Base], Iterator[Mutant]]]:
    def register(f: Callable[[Base], Iterator[Mutant]]) -> Callable[[Base], Iterator[Mutant]]:
        OPERATORS.append(Operator(name, rules, f, rerun))
        return f
    return register


# --- reading the base -----------------------------------------------------------------------

def prose(path: Path) -> list[tuple[int, int]]:
    """(start, end) offsets of the text a reader sees: no TeX comment, front matter or code."""
    text = path.read_text()
    spans, i = [], 0
    if path.suffix == ".md" and text.startswith("+++"):
        i = text.find("\n+++", 3) + 4
    fence = False
    for line in text[i:].splitlines(keepends=True):
        s = line.lstrip()
        if path.suffix == ".md" and s.startswith("```"):
            fence = not fence
        elif not fence:
            end = len(line)
            if path.suffix == ".tex":
                m = re.search(r"(?<!\\)%", line)
                end = m.start() if m else end
            spans.append((i, i + end))
        i += len(line)
    return spans


def printed_numbers(path: Path) -> Iterator[tuple[re.Match[str], int]]:
    """Numbers of the paper's own prose: a number inside a quotation is the source's."""
    text = path.read_text()
    for s, e in prose(path):
        quotes = [q.span() for q in QUOTE.finditer(text, s, e)]
        for m in NUMBER.finditer(text, s, e):
            if not any(a <= m.start() < b for a, b in quotes):
                yield m, line_of(text, m.start())


def leaves(x: Any) -> Iterator[Decimal]:
    if isinstance(x, dict):
        for k, v in x.items():
            if k != "provenance":
                yield from leaves(v)
    elif isinstance(x, list):
        for v in x:
            yield from leaves(v)
    elif isinstance(x, (int, float)) and not isinstance(x, bool):
        try:
            yield Decimal(str(x))
        except InvalidOperation:
            pass


def results_values(base: Base) -> set[Decimal]:
    out: set[Decimal] = set()
    for f in base.results:
        try:
            out.update(leaves(json.loads(f.read_text())))
        except ValueError:
            continue
    return out


def value(printed: str) -> tuple[Decimal, int, bool]:
    """(value, decimals, is a percentage) of a printed number."""
    pct = printed.endswith("%")
    digits = printed.rstrip("%").rstrip("\\").replace(",", "")
    return Decimal(digits), len(digits.split(".")[1]) if "." in digits else 0, pct


def appears(printed: str, values: set[Decimal]) -> bool:
    """`printed` reads as some results value: the perturbed number would trace, by luck."""
    v, d, pct = value(printed)
    q = Decimal(1).scaleb(-d)
    return any((x * 100 if pct else x).quantize(q) == v for x in values
               if abs(x) < Decimal("1e15"))


def unescaped(printed: str) -> str:
    return printed.replace("\\%", "%")


def perturb(digits: str) -> str:
    """The same number with its last digit changed: a typo, or a stale copy."""
    i = max(i for i, c in enumerate(digits) if c.isdigit())
    return digits[:i] + str((int(digits[i]) + 5) % 10) + digits[i + 1:]


def sample[T](items: list[T], limit: int, seed: str) -> list[T]:
    return items if len(items) <= limit else random.Random(seed).sample(items, limit)


def quoted(base: Base) -> list[tuple[Path, re.Match[str], re.Match[str], int]]:
    """(file, quote, its citation, entry index) of every quote cited with an entry, in TeX."""
    keys = {q.get("key"): i for i, q in enumerate(base.quotes)}
    out = []
    for f in base.tex:
        text = f.read_text()
        for s, e in prose(f):
            for q in QUOTE.finditer(text, s, e):
                c = CITE.search(text, q.end(), e)
                if c and text[q.end():c.start()].count(".") == 0:
                    key = next((k.strip() for k in c.group(2).split(",") if k.strip() in keys),
                               None)
                    if key is not None:
                        out.append((f, q, c, keys[key]))
    return out


def front_matter_schema(path: Path) -> re.Match[str] | None:
    text = path.read_text()
    if not text.startswith("+++"):
        return None
    end = text.find("\n+++", 3)
    return re.compile(r'^schema\s*=\s*"[^"]*"\n', re.M).search(text, 0, end)


def write_json(path: Path, doc: Any) -> None:
    path.write_text(json.dumps(doc, indent=1) + "\n")


def edit_json(path: Path, f: Callable[[dict[str, Any]], None]) -> None:
    doc = json.loads(path.read_text())
    f(doc)
    write_json(path, doc)


def append(path: Path, text: str) -> None:
    with path.open("a") as fh:
        fh.write(text)


# Edits of a copy, as `partial(edit, *args)` called with the copy's root last.

def at(rel: str, s: int, e: int, new: str, r: Path) -> None:
    replace_at(r / rel, s, e, new)


def appended(rel: str, text: str, r: Path) -> None:
    append(r / rel, text)


def paragraph(rel: str, para: str, r: Path) -> None:
    add_paragraph(r / rel, para)


def deleted(rel: str, r: Path) -> None:
    (r / rel).unlink()


def written(rel: str, doc: Any, r: Path) -> None:
    write_json(r / rel, doc)


def provenance_dropped(rel: str, r: Path) -> None:
    edit_json(r / rel, lambda d: d.pop("provenance"))


def provenance_schema_dropped(rel: str, r: Path) -> None:
    edit_json(r / rel, lambda d: d["provenance"].pop("schema"))


def reindented(rel: str, r: Path) -> None:
    (r / rel).write_text(json.dumps(json.loads((r / rel).read_text()), indent=4) + "\n")


def tex_end(path: Path) -> int:
    """The line a paragraph appended to `path` starts on (before \\end{document} if any)."""
    text = path.read_text()
    i = text.find("\\end{document}")
    return line_of(text, i) + 1 if i >= 0 else text.rstrip("\n").count("\n") + 3


def add_paragraph(path: Path, para: str) -> None:
    text = path.read_text()
    i = text.find("\\end{document}")
    if i < 0:
        path.write_text(text.rstrip("\n") + "\n\n" + para + "\n")
    else:
        path.write_text(text[:i] + "\n" + para + "\n\n" + text[i:])


# --- numbers ----------------------------------------------------------------------------------

@operator("number-typo", "numbers/untraced", "numbers/chance-match")
def number_typo(base: Base, limit: int = 5) -> Iterator[Mutant]:
    values = results_values(base)
    targets = [(f, m.start(), m.end(), m.group(0), line)
               for f in base.tex + base.md for m, line in printed_numbers(f)]
    targets = [t for t in targets if not appears(perturb(t[3]), values)]
    for f, s, e, old, line in sample(targets, limit, "number-typo"):
        new = perturb(old)
        rel = base.rel(f)
        yield Mutant("number-typo", f"{rel}:{line} {old}->{new}", ("numbers",),
                     partial(at, rel, s, e, new),
                     Expect(("numbers/untraced", "numbers/chance-match"), rel, line),
                     frozenset({"numbers/unused", "numbers/derive-mismatch"}))


@operator("derive-wrong", "numbers/derive-mismatch")
def derive_wrong(base: Base, limit: int = 3) -> Iterator[Mutant]:
    """The author typed a wrong number and declared it derived: value and paper agree, the
    expression does not."""
    entries = base.audit.get("numbers", {}).get("derive", [])
    for i, d in sample(list(enumerate(entries)), limit, "derive-wrong"):
        old = d.get("value", "")
        files = [f for f in base.tex + base.md if any(unescaped(m.group(0)) == old
                                                      for m, _ in printed_numbers(f))]
        if not files:
            continue
        new = perturb(old)

        yield Mutant("derive-wrong", f"numbers.derive[{i}] {old}->{new}", ("numbers",),
                     partial(derive_wrong_edit, i, old, new, [base.rel(f) for f in files]),
                     Expect(("numbers/derive-mismatch",), "audit.toml"))


def derive_wrong_edit(i: int, old: str, new: str, files: list[str], r: Path) -> None:
    s, e = toml_entry_span(r, "numbers.derive", i)
    entry = (r / "audit.toml").read_text()[s:e]
    replace_at(r / "audit.toml", s, e, entry.replace(toml_str(old), toml_str(new), 1))
    for rel in files:
        p = r / rel
        for m, _ in sorted(printed_numbers(p), key=lambda x: -x[0].start()):
            if unescaped(m.group(0)) == old:
                replace_at(p, m.start(), m.end(), perturb(m.group(0)))


@operator("derive-unknown", "numbers/derive-unknown")
def derive_unknown(base: Base, limit: int = 2) -> Iterator[Mutant]:
    entries = base.audit.get("numbers", {}).get("derive", [])
    for i, d in sample(list(enumerate(entries)), limit, "derive-unknown"):
        m = re.search(r"[A-Za-z_][\w-]*", d.get("expr", ""))
        if not m:
            continue

        def edit(r: Path, i: int = i, name: str = m.group(0)) -> None:
            s, e = toml_entry_span(r, "numbers.derive", i)
            entry = (r / "audit.toml").read_text()[s:e]
            replace_at(r / "audit.toml", s, e,
                       re.sub(rf"\b{re.escape(name)}\b", "benchnosuchfile", entry, count=1))

        yield Mutant("derive-unknown", f"numbers.derive[{i}] {m.group(0)}", ("numbers",), edit,
                     Expect(("numbers/derive-unknown",), "audit.toml"),
                     frozenset({"numbers/untraced", "numbers/chance-match"}))


@operator("allow-orphan", "numbers/unused")
def allow_orphan(base: Base) -> Iterator[Mutant]:
    yield Mutant("allow-orphan", "audit.toml", ("numbers",),
                 lambda r: append(r / "audit.toml", '\n[[numbers.allow]]\nvalue = "31,337"\n'
                                  'reason = "an allow entry that outlived its sentence"\n'),
                 Expect(("numbers/unused",), "audit.toml"))


@operator("exploratory-unlabelled", "numbers/exploratory-unlabelled")
def exploratory_unlabelled(base: Base, limit: int = 2) -> Iterator[Mutant]:
    if not base.tex:
        return
    others: set[Decimal] = set()
    explored: set[Decimal] = set()
    for f in base.results:
        try:
            doc = json.loads(f.read_text())
        except ValueError:
            continue
        mode = (doc.get("provenance") or {}).get("mode") if isinstance(doc, dict) else None
        (explored if mode == "exploratory" else others).update(leaves(doc))
    only = sorted(v for v in explored - others if v == v.to_integral_value() and v > 1)
    tex = base.tex[0]
    rel, line = base.rel(tex), tex_end(tex)
    for v in sample(only, limit, "exploratory-unlabelled"):
        yield Mutant("exploratory-unlabelled", f"{rel} {v}", ("numbers",),
                     partial(paragraph, rel, f"The split has {v:,} rows."),
                     Expect(("numbers/exploratory-unlabelled",), rel, line))


@operator("chance-match", "numbers/chance-match")
def chance_match(base: Base) -> Iterator[Mutant]:
    """Dense results (1000 scores) and a printed number that only they trace, by chance."""
    patterns = base.audit.get("results", {}).get("files", [])
    dense = "results/benchdense.json"
    if not base.tex or not any(Path(dense).match(p) for p in patterns):
        return
    tex = base.tex[0]
    rel, line = base.rel(tex), tex_end(tex)

    def edit(r: Path) -> None:
        (r / "results").mkdir(exist_ok=True)
        write_json(r / dense, {"scores": [i / 1000 for i in range(1000)]})
        add_paragraph(r / rel, "The mean score is 0.427.")

    yield Mutant("chance-match", rel, ("numbers",), edit,
                 Expect(("numbers/chance-match",), rel, line))


# --- quotes -----------------------------------------------------------------------------------

@operator("quote-misquote", "quotes/mismatch")
def quote_misquote(base: Base, limit: int = 5) -> Iterator[Mutant]:
    for f, q, _, _ in sample(quoted(base), limit, "quote-misquote"):
        w = re.search(r"\b[A-Za-z]{4,}\b", q.group(1))
        if not w:
            continue
        rel, line = base.rel(f), line_of(f.read_text(), q.start())
        s, e = q.start(1) + w.start(), q.start(1) + w.end()
        yield Mutant("quote-misquote", f"{rel}:{line} {w.group(0)}", ("quotes",),
                     partial(at, rel, s, e, "other"),
                     Expect(("quotes/mismatch",), rel, line), frozenset({"quotes/unused"}))


@operator("quote-wrong-page", "quotes/page")
def quote_wrong_page(base: Base, limit: int = 5) -> Iterator[Mutant]:
    for f, q, c, i in sample(quoted(base), limit, "quote-wrong-page"):
        p = PAGE.search(c.group(1))
        if not p or "page" not in base.quotes[i]:
            continue
        rel, line = base.rel(f), line_of(f.read_text(), q.start())
        s = c.start(1) + p.start(2)
        new = str(int(p.group(2)) + 100)
        yield Mutant("quote-wrong-page", f"{rel}:{line} p.{p.group(2)}->{new}", ("quotes",),
                     partial(at, rel, s, s + len(p.group(2)), new),
                     Expect(("quotes/page",), rel, line))


@operator("quote-no-entry", "quotes/no-entry")
def quote_no_entry(base: Base, limit: int = 3) -> Iterator[Mutant]:
    seen: set[int] = set()
    for f, q, _, i in quoted(base):
        if i in seen or len(seen) >= limit:
            continue
        seen.add(i)
        rel, line = base.rel(f), line_of(f.read_text(), q.start())

        def edit(r: Path, i: int = i) -> None:
            s, e = toml_entry_span(r, "quotes", i)
            replace_at(r / "audit.toml", s, e, "")

        yield Mutant("quote-no-entry", f"quotes[{i}] {base.quotes[i].get('key')}", ("quotes",),
                     edit, Expect(("quotes/no-entry",), rel, line),
                     frozenset({"quotes/no-entry"}))  # every quote of that key loses its entry


def quote_field(i: int, pattern: str, new: str, r: Path) -> None:
    s, e = toml_entry_span(r, "quotes", i)
    entry = (r / "audit.toml").read_text()[s:e]
    replace_at(r / "audit.toml", s, e, re.sub(pattern, new, entry, count=1, flags=re.M))


@operator("quote-unverified", "quotes/unverified")
def quote_unverified(base: Base, limit: int = 2) -> Iterator[Mutant]:
    used = sorted({i for *_, i in quoted(base)})
    for i in sample(used, limit, "quote-unverified"):
        if base.quotes[i].get("status") != "verified":
            continue
        yield Mutant("quote-unverified", f"quotes[{i}]", ("quotes",),
                     partial(quote_field, i, r'^status\s*=\s*"verified"', 'status = "unverified"'),
                     Expect(("quotes/unverified",), "audit.toml",
                            toml_entry_line(base.root, "quotes", i)))


@operator("quote-no-page", "quotes/no-page")
def quote_no_page(base: Base, limit: int = 2) -> Iterator[Mutant]:
    used = [i for i in sorted({i for *_, i in quoted(base)})
            if "page" in base.quotes[i] and base.quotes[i].get("version") != "abstract"]
    for i in sample(used, limit, "quote-no-page"):
        yield Mutant("quote-no-page", f"quotes[{i}]", ("quotes",),
                     partial(quote_field, i, r'^page\s*=.*\n', ""),
                     Expect(("quotes/no-page",), "audit.toml"), frozenset({"quotes/page"}))


@operator("quote-orphan", "quotes/unused")
def quote_orphan(base: Base) -> Iterator[Mutant]:
    entry = ('\n[[quotes]]\nkey = "bench2026orphan"\n'
             'text = "A quote the paper no longer cites."\nversion = "publisher"\n'
             'url = "https://example.org/bench"\npage = "1"\ndate = "2026-01-01"\n'
             'status = "verified"\n')
    yield Mutant("quote-orphan", "audit.toml", ("quotes",),
                 lambda r: append(r / "audit.toml", entry),
                 Expect(("quotes/unused",), "audit.toml"))


# --- results ----------------------------------------------------------------------------------

def git_results(base: Base) -> list[tuple[Path, dict[str, Any]]]:
    return base.provenanced() if base.is_git else []


@operator("results-strip-provenance", "results/no-provenance")
def strip_provenance(base: Base, limit: int = 2) -> Iterator[Mutant]:
    for f, _ in sample(base.provenanced(), limit, "strip-provenance"):
        rel = base.rel(f)
        yield Mutant("results-strip-provenance", rel, ("results",),
                     partial(provenance_dropped, rel),
                     Expect(("results/no-provenance",), rel),
                     frozenset({"results/unused"}))


@operator("instrument-edit", "results/stale")
def instrument_edit(base: Base, limit: int = 2) -> Iterator[Mutant]:
    results = git_results(base)
    if not results:
        return
    for inst in sample(base.instruments, limit, "instrument-edit"):
        rel = base.rel(inst)
        stale = [base.rel(f) for f, p in results if any(rel in str(c) for c in p["command"])]
        if not stale:
            continue

        def edit(r: Path, rel: str = rel) -> None:
            append(r / rel, "\n# changed after the results were made\n")
            commit(r)

        yield Mutant("instrument-edit", rel, ("results",), edit,
                     Expect(("results/stale",), stale[0]), frozenset({"results/stale"}))


def file_inputs(base: Base) -> list[tuple[str, str]]:
    """(results file, input path) of every recorded input that is a file present in the base."""
    return [(base.rel(f), i["path"]) for f, p in base.provenanced()
            for i in p.get("inputs", []) if (base.root / i["path"]).is_file()]


@operator("input-edit", "results/input-changed")
def input_edit(base: Base, limit: int = 2) -> Iterator[Mutant]:
    for res, inp in sample(file_inputs(base), limit, "input-edit"):
        yield Mutant("input-edit", inp, ("results", "--inputs"),
                     partial(appended, inp, "\n"),
                     Expect(("results/input-changed",), res),
                     frozenset({"results/input-changed"}))


@operator("input-delete", "results/input-missing")
def input_delete(base: Base, limit: int = 2) -> Iterator[Mutant]:
    for res, inp in sample(file_inputs(base), limit, "input-delete"):
        yield Mutant("input-delete", inp, ("results", "--inputs"),
                     partial(deleted, inp),
                     Expect(("results/input-missing",), res),
                     frozenset({"results/input-missing"}))


@operator("results-hand-edit", "results/not-reproduced", rerun=True)
def results_hand_edit(base: Base, limit: int = 2) -> Iterator[Mutant]:
    for f, _ in sample(git_results(base), limit, "results-hand-edit"):
        doc = json.loads(f.read_text())
        key = next((k for k, v in doc.items() if k != "provenance"
                    and isinstance(v, (int, float)) and not isinstance(v, bool)), None)
        if key is None:
            continue
        rel = base.rel(f)

        def edit(r: Path, rel: str = rel, key: str = key) -> None:
            def bump(d: dict[str, Any]) -> None:
                d[key] = d[key] + 1
            edit_json(r / rel, bump)

        yield Mutant("results-hand-edit", f"{rel} {key}", ("results", "--rerun"), edit,
                     Expect(("results/not-reproduced",), rel))


@operator("wall-clock", "results/wall-clock")
def wall_clock(base: Base, limit: int = 2) -> Iterator[Mutant]:
    if not git_results(base):
        return
    for inst in sample([p for p in base.instruments if p.suffix == ".py"], limit, "wall-clock"):
        rel = base.rel(inst)
        line = inst.read_text().count("\n") + 1

        def edit(r: Path, rel: str = rel) -> None:
            text = (r / rel).read_text()
            (r / rel).write_text(text + ("" if text.endswith("\n") else "\n")
                                 + "import time; _t = time.time()\n")
            commit(r)

        yield Mutant("wall-clock", rel, ("results",), edit,
                     Expect(("results/wall-clock",), rel, line), frozenset({"results/stale"}))


@operator("depends-orphan", "results/unused")
def depends_orphan(base: Base) -> Iterator[Mutant]:
    if not base.instruments:
        return
    inst = base.rel(base.instruments[0])
    yield Mutant("depends-orphan", "audit.toml", ("results",),
                 lambda r: append(r / "audit.toml", '\n[[results.depends]]\n'
                                  'file = "results/bench-gone.json"\n'
                                  f'instruments = [{toml_str(inst)}]\n'),
                 Expect(("results/unused",), "audit.toml"))


# --- study ------------------------------------------------------------------------------------

def confirmatory(base: Base) -> list[tuple[Path, dict[str, Any]]]:
    return base.provenanced("confirmatory") if base.is_git else []


def set_prov(rel: str, **kv: Any) -> Callable[[Path], None]:
    def edit(r: Path) -> None:
        edit_json(r / rel, lambda d: d["provenance"].update(kv))
    return edit


@operator("study-unregistered", "study/not-preregistered")
def study_unregistered(base: Base, limit: int = 2) -> Iterator[Mutant]:
    for f, _ in sample(confirmatory(base), limit, "study-unregistered"):
        rel = base.rel(f)
        yield Mutant("study-unregistered", rel, ("study",),
                     set_prov(rel, study="prereg/bench-missing.md"),
                     Expect(("study/not-preregistered",), rel))


@operator("study-other-experiment", "study/prereg-mismatch")
def study_other_experiment(base: Base, limit: int = 2) -> Iterator[Mutant]:
    for f, p in sample(confirmatory(base), limit, "study-other-experiment"):
        rel = base.rel(f)
        cmd = list(p["command"])
        i = next((i for i, c in enumerate(cmd) if (base.root / c).is_file()
                  and Path(c).suffix in (".py", ".sh", ".R", ".jl")), None)
        if i is None:
            continue
        cmd[i] = str(Path(cmd[i]).with_name("bench_other" + Path(cmd[i]).suffix))
        yield Mutant("study-other-experiment", rel, ("study",), set_prov(rel, command=cmd),
                     Expect(("study/prereg-mismatch",), rel))


@operator("study-silent-amendment", "study/amended")
def study_silent_amendment(base: Base, limit: int = 2) -> Iterator[Mutant]:
    """The bar moved in the pre-registration, then a confirmatory run at that commit."""
    for f, p in sample(confirmatory(base), limit, "study-silent-amendment"):
        rel, prereg = base.rel(f), p.get("study", "")
        if not (base.root / prereg).is_file() or not re.search(
                r"^alpha\s*=", (base.root / prereg).read_text(), re.M):
            continue

        def edit(r: Path, rel: str = rel, prereg: str = prereg) -> None:
            text = (r / prereg).read_text()
            (r / prereg).write_text(re.sub(r"^alpha\s*=\s*([0-9.eE-]+)",
                                           lambda m: f"alpha = {float(m.group(1)) * 2}",
                                           text, count=1, flags=re.M))
            commit(r)
            set_prov(rel, commit=head(r))(r)

        yield Mutant("study-silent-amendment", f"{rel} {prereg}", ("study",), edit,
                     Expect(("study/amended",), rel))


@operator("study-peeked", "study/data-reused")
def study_peeked(base: Base, limit: int = 2) -> Iterator[Mutant]:
    """An unlabelled run on the confirmatory data, a day before the confirmatory run."""
    for f, p in sample(confirmatory(base), limit, "study-peeked"):
        rel = base.rel(f)
        peek = {k: v for k, v in p.items() if k not in ("mode", "study")}
        peek["now"] = (datetime.fromisoformat(p["now"]) - timedelta(days=1)).isoformat()
        out = str(Path(rel).with_name(Path(rel).stem + "_benchpeek.json"))
        yield Mutant("study-peeked", rel, ("study",),
                     partial(written, out, {"peek": 1, "provenance": peek}),
                     Expect(("study/data-reused",), rel))


# --- schema -----------------------------------------------------------------------------------

def schema_targets(base: Base) -> list[Path]:
    dirs = [base.study_dir("prereg"), base.study_dir("explore")]
    return [p for p in base.md + [m for d in dirs for m in sorted(d.glob("*.md"))]
            if front_matter_schema(p)]


@operator("schema-drop", "schema/missing")
def schema_drop(base: Base, limit: int = 3) -> Iterator[Mutant]:
    for p in sample(schema_targets(base), limit, "schema-drop"):
        rel = base.rel(p)

        def edit(r: Path, rel: str = rel) -> None:
            m = front_matter_schema(r / rel)
            assert m
            replace_at(r / rel, m.start(), m.end(), "")

        yield Mutant("schema-drop", rel, ("schema",), edit, Expect(("schema/missing",), rel, 1))
    for f, _ in sample([x for x in base.provenanced() if "schema" in x[1]], 1, "schema-drop"):
        rel = base.rel(f)
        yield Mutant("schema-drop", rel, ("schema",), partial(provenance_schema_dropped, rel),
                     Expect(("schema/missing",), rel))


@operator("schema-wrong-kind", "schema/unknown")
def schema_wrong_kind(base: Base, limit: int = 2) -> Iterator[Mutant]:
    for p in sample(schema_targets(base), limit, "schema-wrong-kind"):
        rel = base.rel(p)
        m = front_matter_schema(p)
        assert m
        other = SCHEMAS + ("document-v1.json" if "prereg" not in m.group(0)
                           else "exploration-log-v1.json")
        if other in m.group(0):
            other = SCHEMAS + "prereg-v1.json"
        yield Mutant("schema-wrong-kind", rel, ("schema",),
                     partial(at, rel, m.start(), m.end(), f'schema = "{other}"\n'),
                     Expect(("schema/unknown",), rel, 1))


# --- negatives: edits that keep every claim traced ----------------------------------------------

@operator("neg-comment-number")
def neg_comment_number(base: Base, limit: int = 2) -> Iterator[Mutant]:
    for f in sample(base.tex, limit, "neg-comment-number"):
        rel = base.rel(f)
        yield Mutant("neg-comment-number", rel, ("numbers",),
                     partial(appended, rel, "\n% 31337 in a comment is not printed\n"),
                     None)


@operator("neg-reflow")
def neg_reflow(base: Base, limit: int = 3) -> Iterator[Mutant]:
    """Two consecutive prose lines joined into one: the same text, other line breaks."""
    targets = []
    for f in base.tex:
        lines = f.read_text().splitlines(keepends=True)
        for i in range(len(lines) - 1):
            a, b = lines[i], lines[i + 1]
            if all(x.strip() and x.strip()[0].isalpha() and "%" not in x for x in (a, b)):
                targets.append((base.rel(f), i))
    for rel, i in sample(targets, limit, "neg-reflow"):
        def edit(r: Path, rel: str = rel, i: int = i) -> None:
            lines = (r / rel).read_text().splitlines(keepends=True)
            lines[i] = lines[i].rstrip("\n") + " "
            (r / rel).write_text("".join(lines))
        yield Mutant("neg-reflow", f"{rel}:{i + 1}", ALL, edit, None)


@operator("neg-quote-linebreak")
def neg_quote_linebreak(base: Base, limit: int = 3) -> Iterator[Mutant]:
    for f, q, _, _ in sample(quoted(base), limit, "neg-quote-linebreak"):
        sp = q.group(1).find(" ")
        if sp < 0:
            continue
        rel, s = base.rel(f), q.start(1) + sp
        yield Mutant("neg-quote-linebreak", f"{rel}:{line_of(f.read_text(), s)}", ("quotes",),
                     partial(at, rel, s, s + 1, "\n"), None)


@operator("neg-audit-comment")
def neg_audit_comment(base: Base) -> Iterator[Mutant]:
    yield Mutant("neg-audit-comment", "audit.toml", ALL,
                 lambda r: append(r / "audit.toml", "\n# a comment changes nothing\n"), None)


@operator("neg-unrelated-commit")
def neg_unrelated_commit(base: Base) -> Iterator[Mutant]:
    if not base.is_git:
        return

    def edit(r: Path) -> None:
        (r / "BENCH-NOTES.txt").write_text("not an instrument, not an input\n")
        commit(r)

    yield Mutant("neg-unrelated-commit", "BENCH-NOTES.txt", ALL, edit, None)


@operator("neg-results-reindent")
def neg_results_reindent(base: Base, limit: int = 2) -> Iterator[Mutant]:
    for f in sample(base.results, limit, "neg-results-reindent"):
        rel = base.rel(f)
        try:
            json.loads(f.read_text())
        except ValueError:
            continue
        yield Mutant("neg-results-reindent", rel, ALL, partial(reindented, rel), None)


def mutants(base: Base, rerun: bool = False) -> Iterator[Mutant]:
    for op in OPERATORS:
        if op.rerun and not rerun:
            continue
        yield from op.generate(base)
