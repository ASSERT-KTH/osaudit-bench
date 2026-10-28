# osaudit-bench

Defect-injection benchmark for [osaudit](https://github.com/ASSERT-KTH/osaudit): inject known defects into paper repositories, run osaudit, and report per operator how many defects it detected (recall) and which findings no defect explains (false alarms). Silent when every defect is detected and nothing else is reported. Mutation testing, with osaudit as the test suite.

Every rule osaudit can emit has at least one operator (`--coverage` checks it against osaudit's source). Negative operators make edits that keep every claim traced (a reflowed paragraph, a number in a comment, a line break inside a quote, an unrelated commit, …): any finding they cause is a false alarm.

## Install

    uv venv && uv pip install -e '.[dev]'     # osaudit from PyPI
    uv pip install -e ../osaudit               # or a local checkout of osaudit

## Usage

    osaudit-bench                          # on the built-in synthetic base
    osaudit-bench --base PAPER_REPO ...    # on real paper repositories (with an audit.toml)
    osaudit-bench --base PAPER_REPO --synth
    osaudit-bench --rerun                  # also operators needing `osaudit results --rerun`
    osaudit-bench --operator number-typo   # one operator (repeatable)
    osaudit-bench --json                   # {summary, outcomes}
    osaudit-bench --list-operators
    osaudit-bench --coverage               # osaudit rules no operator targets; silent when none

Each mutant is applied to a fresh copy of its base (git history included); the base is never touched. A base need not be clean: the findings osaudit already reports on it are subtracted. A positive mutant is *detected* when a new finding has its expected rule in its expected file, *located* when the line agrees too.

Output, when something is missed:

    number-typo                   14/15  detected   14 located    0 false alarms  0 errors
    survivor  base: number-typo paper.tex:10 2023->2028: expected {...}
    alarm     base: neg-reflow paper.tex:4: paper.tex:5: numbers/untraced: ...

A survivor is either a gap in osaudit or a mutant that changed no claim (an equivalent mutant); each one is read before it is counted against osaudit. Exit status: 0 all detected and no false alarm, 1 otherwise, 2 bad usage.

## Targets

Targets are found with the benchmark's own regular expressions and a tomllib read of `audit.toml`, not with osaudit's extraction, so a bug in osaudit's reading cannot hide what it should check. A perturbed number that reads as some other results value is not generated: it would trace, legitimately.
