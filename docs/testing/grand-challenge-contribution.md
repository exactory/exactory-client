# Test record: Grand Challenge direction and contribution development

Design: `misc/harness-improvement/v6/grand-challenge-contribution-design.md` in the exactory repository.
Each task was written test first. The RED line is the failure seen before the implementation; the GREEN lines are
the suites run after it. Suites marked "(frozen copy)" ran on a copy of the tree taken at that task.

| Task | Command | Result |
| --- | --- | --- |
| T01 baseline on 3e14b24 (archive copy) | `python3 -m unittest discover -s tests` | Ran 1155 tests in 2603.015s OK (skipped=1) |
| T02 RED | `test_research_publication.py` | the two new tests errored: the validator refused the ninth field (`Expected fields: ...`) |
| T02 GREEN | `test_research_publication.py` | Ran 22 tests OK |
| T02 (frozen copy) | `test_research_rounds.py`, `test_research_round_integrity.py`, `test_research_review_packets.py`, `test_draft.py`, `test_research_cli.py` | Ran 63, 12, 51, 81, 23 tests OK |
