# Development-round integrity validation

Baseline: `6c74acb`, the head of PR 18 and release 0.39.1.
Release under validation: 0.39.2.

## Reproductions

- The baseline round module passed 62 tests in 676.345 seconds on Python 3.9.6.
- Four of the first five regression tests failed against the baseline. Direct
  production deposit reached the fake remote API without a round decision.
  Blind delivery included claim history. Measurement included publication
  reviewers, and incomplete measurements lacked an explicit completion state.
- After the three fixes, all five tests passed in 29.124 seconds.
- An additional preview test failed before its currency check was added.
  A PDF change during the metadata request still allowed a remote preview write.
- The extended nine-test module then passed in 66.214 seconds.
- A new-version regression confirmed that a changed bundle also needs its own
  approved stop. The preview GET regression reproduced a bundle change between
  the remote read and the preview PUT; the service now checks currency after
  that read and before recording a pending write.
- The final eleven-test regression module passed in 85.620 seconds.

The tests use real study operations and content objects. Zenodo is an in-memory
fake at the external transport boundary. No test publishes a real paper.

## Coverage

`tests/test_research_round_integrity.py` covers the publication boundary and
resumption, current-claim delivery, retained internal history, separate round
packets, paired measurement populations, and incomplete or ambiguous groups.
Existing publication fixtures now obtain an approved stop through the real
decision and independent-review operations before deposit.

The existing measurement test retains its legacy-review supersession checks.
Its expected measurement population changes from five reviewers to the three
reviewers with predictions. Publication still sees the full review population.

## Final validation

On Python 3.9.6, `python3 -m unittest discover -s tests -v` passed all 994 tests
in 1,956.675 seconds. The full run includes the eleven new regression tests.
All Python sources compile. Codex generation verification, JSON validation,
and `git diff --check` pass.

Two independent reviewers inspected the complete implementation and its tests.
Their findings, including the preview read/write gap, are resolved. A separate
skill scenario review confirmed current-claim delivery, paired measurement,
mandatory stops for revised deposits, and retention of ambiguous measurements
until a substantive manuscript revision. No unresolved release blocker remains
in those reviews. The separate math-solver suite and the three supported Python
versions are also checked by the release PR's CI.

## Research outcomes

These tests use synthetic studies. They establish no measured improvement in
research contribution, cohort prediction, reference quality, or research cost.
Those outcomes require the real-study protocol in the development-round design.
