# Test record: Grand Challenge direction and contribution development

Design: `misc/harness-improvement/v6/grand-challenge-contribution-design.md` in the exactory repository.
Each task was written test first. The RED line is the failure seen before the implementation; the GREEN lines are
the suites run after it. Suites marked "(frozen copy)" ran on a copy of the tree taken at that task.

| Task | Command | Result |
| --- | --- | --- |
| T01 baseline on 3e14b24 (archive copy) | `python3 -m unittest discover -s tests` | Ran 1155 tests in 2603.015s OK (skipped=1) |
