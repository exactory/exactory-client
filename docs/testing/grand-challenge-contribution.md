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
| T03 RED | `test_research_literature.py -k grand_challenge`, `test_research_contribution.py` | `invalid_search`: the purpose was unknown |
| T03 GREEN | `test_research_literature.py`, `test_research_contribution.py`, `test_research_lineage.py` | OK, OK, OK |
| T04 RED | `test_research_challenge.py` | ImportError: `research_harness.challenge` did not exist |
| T04 GREEN | `test_research_challenge.py`, `test_research_synthesis.py` | Ran 5 tests OK; Ran 34 tests OK after three hand-built syntheses recorded the Grand Challenge |
| T04 (frozen copy) | development, cli, guidance, report_views, publication, literature, lineage | Ran 65, 23, 5, 12, 22, 24, 8 tests OK |
| T05 RED | `test_research_principles.py` | `AssertionError: '4' != '5'` |
| T05 GREEN | `test_research_principles.py`, `test_research_guidance.py` | OK, OK |
| T06 RED | `test_research_contribution.py` | ImportError: `research_harness.contribution` did not exist |
| T06 GREEN | `test_research_contribution.py` | Ran 10 tests OK |
