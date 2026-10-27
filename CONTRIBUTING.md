# Contributing

- Tests: stdlib `unittest` only, no third-party test deps; must pass in BOTH
  runners — `python tests/test_x.py` and `python -m unittest discover -s tests` —
  and under an empty `HOME` (CI reproduces a clean runner).
- No style gate (no ruff/black CI step by design). `python -m compileall` must pass.
- New trigger/exclusion patterns: add a test case with the *exact* sentence that
  motivated it (real-world quote beats invented example).
- Keep detector.py transport-only: regex decides *whether* to consider nudging;
  whether the question was legitimate stays with the agent (nudge prompt).
