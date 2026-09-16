# User-authorized publication validation

Baseline: `4692b84`, the design and plan commit on `feat/user-authorized-publication`.
Its code is `fc081fd`, the head of PR 19 and release 0.39.2.
Release under validation: 0.40.0.

## Reproductions

Every task ran its tests against the unchanged code first. The measured results,
on Python 3.9.6:

- Removing the two Bash gate hooks failed all three tests of the hook manifest
  (`Ran 3 tests in 0.008s FAILED (failures=3)`). After `hooks/hooks.json` and the
  tests changed, `test_hooks` passed 38 tests and `test_codex` 29.
- The new decision-helper module failed at import
  (`ImportError: cannot import name 'note_managed_record_skipped'`). After the
  helpers existed it passed 10 tests, and 13 with the help-text tests of Task 7.
- `exactory verify` from a directory with no workspace raised
  `migration_required`, and in a workspace that had not bound the verdict it
  raised `verdict_assessment_required`
  (`Ran 6 tests in 1.528s FAILED (errors=2)`). After the change the six tests
  passed, and the whole transport module passed 92 tests.
- `exactory submit` refused inside a workspace whose citation report was missing
  and inside a study whose publication gate was not ready
  (`Ran 5 tests in 16.545s FAILED (errors=3)`). After the change the transport
  module passed 95 tests.
- `exactory-draft deposit` refused every direct-path case
  (`Ran 13 tests in 12.991s FAILED (failures=3, errors=10)`), refused a first
  deposit and a new version without an approved stop
  (`Ran 6 tests in 58.133s FAILED (errors=2)`), and refused a bare draft marker
  before reading the Zenodo token (`Ran 1 test in 0.120s FAILED (failures=1)`).
  After the change `test_draft` passed 63 tests, and the gate, round-integrity
  and publication modules passed 29.
- Restoring the three skills from their 0.36.0 text failed four guidance tests,
  because every shared skill links the constitution and the primary stages link
  the workflow (`Ran 5 tests in 7.404s FAILED (failures=4)`). After the link
  sentences the five tests passed.
- The documentation and version changes failed three tests of the guidance,
  manifest and Codex modules before the edits
  (`Ran 41 tests in 14.468s FAILED (failures=3)`) and passed all 41 after.

The exactory API and Zenodo are in-memory fakes at the transport boundary. No
test publishes a paper, sends a verdict, or opens a verification request.

One measurement note: `python3 -m unittest tests.<module>` does not put `tests/`
on `sys.path`, so a module whose test imports a sibling helper fails to import
it. This is true on the baseline as well. The runs above use the form CI uses,
`python3 -m unittest discover -s tests`, or `PYTHONPATH=tests`.

## Coverage

`tests/test_user_authorized_publication.py` covers the shared decision point:
no workspace, a draft workspace with no store, a store with no research
configuration, a corrupt store, a refused check, a passing check from a
subdirectory, the note and its pending obligation codes, the citation report,
and the help text of the three commands.

`tests/test_transport.py` covers `exactory submit` and `exactory verify` on both
paths: the printed citation report, the silent case outside a workspace, a study
that records its receipt, a study that does not, a verification workspace that
bound the verdict, one that did not, a body that differs from the bound
assessment, and a verdict sent after an unknown earlier outcome.

`tests/test_draft.py` covers `exactory-draft deposit` on the managed path, the
direct path in a workspace with a store, a workspace with no store, and a
workspace whose store does not open. It covers the local record: the store
record that a projection export keeps, the file written without a store, the
symlink that the projection primitive replaces instead of following, and the
store that refuses the record. It covers every local input the direct path
reads before its first remote write.

`tests/test_research_round_integrity.py` and `tests/test_research_gates.py` keep
the managed boundaries: a bundle that changes after the managed remote steps
started still stops that deposit, and a pending managed intent still needs its
approved stop before it resumes.

## Review

Seven implementers wrote the seven tasks. Two independent reviewers read each
task's diff in every round, one for spec compliance and one for correctness and
the project's code principles. A task was approved only when both reviewers
reported no blocker and no major finding. Five fix passes ran, and the review
loop then ran again with fresh reviewers. Thirty-six agents ran in total.

The findings that changed the code:

- The message of `verdict_reconciliation_pending` ended with "no duplicate POST
  was sent". The decision point now prints that message and then sends the
  verdict directly, so the sentence was no longer true. It was removed.
- The revived abstract reader had no handler for a decode failure or a
  directory, so a non-UTF-8 file ended the command with a traceback.
- The direct deposit created the Zenodo record before it read the upload files,
  so an unreadable file left a draft record behind. Every local input is now
  read first.
- Both branches that write `.exactory/deposit.json` hand-wrote the file, which
  followed a symlink. They now use the projection primitive the export uses.
- The store was opened twice for one deposit, so one refusal printed two notes.
- The module docstring and the CLI reference claimed that every non-managed path
  prints a note. A workspace with no store prints nothing.

One finding was left in place on purpose. `check_verdict_preconditions` records
the task's own verdict identifier as a remote observation before it raises for
an unknown earlier outcome. The record is true on both paths, and it keeps what
the task showed before the direct POST.

## Final validation

On Python 3.9.6, `python3 -m unittest discover -s tests` ran 1,009 tests in
1,882.286 s and passed. The baseline ran 994 tests in 1,889.338 s and passed.

The math-solver harness suite, which this release does not touch, ran alone and
passed 689 tests in 1,667.159 s.

`python3 -m compileall -q bin hooks research_harness codex tests`,
`python3 codex/generate.py --check`, `python3 -m json.tool` over every tracked
JSON file, and `git diff --check` all exit 0.

These repairs establish that the three commands run on the user's instruction
and that a ready study still records its receipt. They do not measure how often
a study reaches its gates, which the development-round protocol measures.
