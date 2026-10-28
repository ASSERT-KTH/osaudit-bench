"""The synthetic base: a small paper repository that uses every convention osaudit audits.

An exploration on the training split, a confirmatory run on the test split registered before it,
a paper and a findings document whose numbers trace to both, quotes with entries, a derivation
and an allowed number. `osaudit all`, `results --inputs` and `results --rerun` are silent on it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ._base import commit, git

SCHEMAS = "https://github.com/ASSERT-KTH/assert-research-agent/blob/main/schemas/"
NOW = "2026-03-02T00:00:00+00:00"
BEFORE = "2026-03-01T00:00:00+00:00"

FILES = {
    "experiments/count.py": """\
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
files = sorted(p.rglob("*.csv")) if p.is_dir() else [p]
rows = [r for f in files for r in f.read_text().splitlines()]
print(json.dumps({"rows": len(rows), "yes": sum(r == "yes" for r in rows)}))
""",
    "data/train.csv": "yes\nno\nno\nyes\n",
    "data/test/part.csv": "yes\nyes\nno\nyes\nno\nyes\nno\nyes\n",
    "prereg/count.md": f"""\
+++
schema = "{SCHEMAS}prereg-v1.json"
mode = "confirmatory"
hypothesis = "more than half of the test rows say yes"
null = "at most half of the test rows say yes"
experiments = ["experiments/count.py"]
data = ["data/test"]
statistic = "one-sided binomial test"
alpha = 0.05
m = 1
correction = "none"
go = "p < 0.05"
stop = "p >= 0.05"
disaster = "rows = 0"
+++

# count
""",
    "explore/look.md": f"""\
+++
schema = "{SCHEMAS}exploration-log-v1.json"
mode = "exploratory"
holdout = ["data/test"]
data_seen = ["data/train.csv"]
+++

# look
""",
    "paper.tex": """\
\\documentclass{article}
\\begin{document}
\\section{Results}
In an exploratory look, the training split has 4 rows.
The confirmatory test read 8 rows,
of which 5 say yes (62.5\\%).
Doe calls the archive ``the second memory of a laboratory'' \\cite[p.~12]{doe2001}.
Roe writes that ``a notebook is read more often than it is written'' \\cite[p.~3]{roe1999}.
The census counts 2,000,000 people \\cite{census}.
\\end{document}
""",
    "findings.md": f"""\
+++
schema = "{SCHEMAS}document-v1.json"
+++

# Findings

The confirmatory test read 8 rows.
""",
    "audit.toml": f"""\
schema = "{SCHEMAS}audit-v1.json"

[paper]
tex = ["paper.tex"]
md = ["findings.md"]

[results]
files = ["results/*.json"]
instruments = ["experiments/*.py"]

[[numbers.derive]]
value = "62.5%"
expr = "count.yes / count.rows"

[[numbers.allow]]
value = "2,000,000"
reason = "population size published by the cited census"
cite = "census"

[[quotes]]
key = "doe2001"
text = "We call the archive the second memory of a laboratory, after the minds of its members."
version = "publisher"
url = "https://doi.org/10.1000/doe2001"
page = "12"
date = "2026-01-10"
status = "verified"

[[quotes]]
key = "roe1999"
text = "A notebook is read more often than it is written."
version = "author-pdf"
url = "https://example.org/roe1999.pdf"
page = "3"
date = "2026-01-11"
status = "verified"
""",
}


def build(root: Path) -> Path:
    """Write the synthetic base into `root` (created, must not exist) and return it."""
    root.mkdir(parents=True)
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    (root / "results").mkdir()
    git(root, "init", "-q")
    commit(root, BEFORE)
    env = {**os.environ, "OSAUDIT_NOW": NOW}
    for flags, data, out in ((["--explore", "explore/look.md"], "data/train.csv", "look"),
                             (["--prereg", "prereg/count.md"], "data/test", "count")):
        subprocess.run([sys.executable, "-m", "osaudit", "run", "--input", data,
                        "-o", f"results/{out}.json", *flags, "--",
                        sys.executable, "experiments/count.py", data],
                       cwd=root, env=env, check=True, capture_output=True)
    commit(root, NOW)
    return root
