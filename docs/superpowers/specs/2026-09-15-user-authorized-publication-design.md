# User-authorized publication design

Date: 2026-09-15

Status: Approved by the user on 2026-09-15 in chat. The user also approved: the citation gate becomes a report that never stops a command, and the cohort prediction stays required by the CLI. Implementation starts when the user says so.

Target version: 0.40.0

Inputs: the plugin at commit `fc081fd` (0.39.2) on `main`; the pre-harness implementations of `exactory submit`, `exactory verify`, and `exactory-draft deposit` at commit `f5b3c46^` (0.36.0); the three skills at tag `exactory--v0.36.0`.

## Objective

The user's instruction is the authorization for `exactory submit`, `exactory verify`, and `exactory-draft deposit`. Each of the three commands runs whenever the user asks, from any directory, on any workspace, at the time the user names. The managed research harness records a receipt when the workspace's current evidence supports one, writes one line to stderr when it cannot, and never refuses the command.

The stage gates of the Exactory AI Science loop (`exactory-lab state set`, every `exactory-research` mutation, and the gates they read) keep their current behavior. A study still closes its `deposit` and `submit` stages through managed receipts; the three commands write those receipts automatically when the study is ready.

## Principle

A user who says "submit", "verify", or "deposit" is exercising a decision that belongs to them. The plugin's job at that moment is to carry the decision out and to tell the user what it recorded. Reading obligations, review counts, round decisions, and citation reports inform the user; they do not veto the user.

## Global constraints

- Python 3.9+ standard library only.
- The SQLite schema stays at `schema_version` 1. Records stay append-only.
- `RESEARCH_CONSTITUTION.md` stays at Version 3 with its current bytes, so no study receives a `constitution_revalidation_required` obligation from this release.
- The server wire API is unchanged: `POST /api/v1/verifications`, `GET /api/v1/tasks/{id}`, `POST /api/v1/verifications/{id}/verdicts`, and the Zenodo deposition API keep their current bodies.
- `exactory verify` keeps refusing a verdict file whose `prediction` is missing or has no `percentile`, before any network call. That rule now lives in one place, the CLI.
- `exactory-draft deposit --production --publish` keeps requiring `--confirm-publish`. The user's instruction to deposit is the approval the flag records.
- Both host manifests carry version `0.40.0`. `codex/generate.py --check` passes.
- All artifacts, code, and documentation are English.

## 1. One rule for three commands

Each command has a managed path (the current 0.39.2 behavior, which validates the workspace's evidence and records intents and receipts in the store) and a direct path (the 0.36.0 behavior, which sends the user's request and prints the server's answer).

The command decides between the two paths before its first network write:

1. Locate a workspace from the current directory (`find_workspace(required=False)`) and open its store. A directory with no workspace, or a workspace whose store is missing or unconfigured (`migration_required`, for example a draft workspace initialized before 0.38.0), selects the direct path with no message: that is the ordinary case for a verifier or an author working outside the loop.
2. Run every check the managed path performs before its first remote write. When all of them pass, take the managed path.
3. When opening the store raises any other `ResearchError` (for example `corrupt_state`), or any check raises `ResearchError`, write one line to stderr and take the direct path:

   ```
   Managed record skipped (<code>): <message>
   ```

   `<code>` and `<message>` are the raised error's `code` and `message`. The line tells the user that the command ran and that the study's receipt was not written.

Once the managed path has started its remote steps, it keeps its current behavior for the rest of that command: intent persistence, reconciliation of an unknown outcome through `exactory reconcile` or `exactory-draft reconcile`, and the exact-target checks that follow a write. Those steps run after the user's request has been sent, so they inform the user about the state of the record; they do not stand between the user and the send.

## 2. `exactory verify`

Preconditions of the managed path, checked in this order after the verdict file and the task are read: a workspace with a store exists; `validate_verdict` passes for that task and body; the prior-intent checks of `send_verdict` pass (no pending unknown outcome for this verification, and a revision names the latest known own verdict id).

Managed path: unchanged (`send_verdict`).

Direct path: `POST /api/v1/verifications/<verificationId>/verdicts` with the file's body, then print the response. The verification id comes from the task the command already fetched.

The task is fetched once through `GET /api/v1/tasks/<identifier>` on both paths, as today. `exactory task --bind` and `exactory-research bind-verdict` remain available for a verification workspace that wants the managed receipt.

## 3. `exactory submit`

Preconditions of the managed path: a workspace with a store exists; `validate_submission` passes (the `deposited` publication gate is ready, exactly one production receipt matches the current bundle, and the submitted DOI or URL names that record).

Managed path: unchanged (`submit_managed`, including `continue_submission` after the POST).

Direct path: `POST /api/v1/verifications` with the body, then print the response and the existing "The server returned the existing open request for this paper." notice on a 200.

Citation report: inside a draft or study workspace the command still runs `exactory-check gate` before the send. A failing gate writes its message to stderr with the prefix `Citation report: ` and the command continues.

## 4. `exactory-draft deposit`

Preconditions of the managed path: a workspace with a store exists; the `readiness` gate is ready; `validate_upload` accepts the PDF, abstract, and optional sources against the current publication bundle; the round gate is ready (an approved `stop` on that bundle); with `--new-version`, the store's workspace deposit record exists on the same environment.

Managed path: unchanged (`research_harness.zenodo.deposit`).

Direct path: the 0.36.0 flow. Create the deposition (or open a new version of the deposition recorded in `.exactory/deposit.json` when `--new-version` is set), upload `paper.pdf` and the optional sources archive, set the metadata, mark `paper.pdf` as the default preview, and publish when `--publish` is set. The PDF may live anywhere on disk; the abstract file is read as UTF-8 text and must contain nonblank text.

The direct path then records the deposit with the same fields the managed projection writes: `environment`, `deposition_id`, `draft_url`, and, once published, `doi`, `concept_doi`, `record_url`. In a workspace with a usable store, `record_direct_deposit` sets the store's workspace deposit record to these fields and exports the projection, so `.exactory/deposit.json` survives the next projection export (for example the one `exactory-lab decide` runs). The record grants no publication credit: the gates read publication receipts bound to a reviewed bundle. In a workspace without a usable store, the command writes `.exactory/deposit.json` itself.

The command prints the deposit state before it keeps the local record, so the DOI reaches the user whatever the store does. A store that exists but refuses the record is the one case the file does not survive: `export_workspace` rewrites `.exactory/deposit.json` from the store, so the written file holds a deposit the store does not know. That case prints the one-line note, writes the file, states that the file is the only local copy and that a projection export can replace it, and exits nonzero.

Both paths keep: the token rule (`ZENODO_SANDBOX_TOKEN` for the sandbox, `ZENODO_TOKEN` for production, the missing variable named in the error), the `--confirm-publish` requirement for a production publish, the disclosure that `_build_deposit_metadata` writes, the fixed upload names, and `--new-version` requiring a prior record on the same environment.

Citation report: a production deposit runs `exactory-check gate` before its first remote write. A failing gate writes its message to stderr with the prefix `Citation report: ` and the deposit continues.

`--new-version` on the direct path reads the prior record from `.exactory/deposit.json`. A missing or malformed file, or a prior record on the other environment, is reported and the command exits, as in 0.36.0: there is no record to revise.

## 5. Hooks

`hooks/enforce_citation_check.py` and `hooks/enforce_prediction.py` are deleted, together with their `hooks/hooks.json` entries. `codex/hooks.json` is regenerated. The remaining hooks (`guard_attack_files`, `guard_experiment_exec`, `enforce_decision_log`, `enforce_unit_flow`, `check_references_edit`, `record_paper_authorship`, `record_attack_activity`, `resume_attack`, `continue_autopilot`, `continue_attack`) are unchanged.

The description field of `hooks/hooks.json` names no script, so it is unchanged.

## 6. Skills

Three skills are rewritten from their 0.36.0 text plus the server rules that arrived since (verdict revision through `supersedesVerdictId`, `requestedByViewer`, `--challenge`, the writer disclosure). Each skill states the one-rule contract of section 1 in one paragraph, so an agent knows that a study's receipt is written automatically when the study is ready and that the command runs either way. Each skill keeps one link to `RESEARCH_CONSTITUTION.md`, and the verify skill keeps one link to `docs/research-workflow.md`, because every shared skill links the constitution and the primary research stages link the workflow; the link sentence names the managed study as its scope.

`skills/submit/SKILL.md` keeps: the API-key handling, the identifier forms, `--challenge`, the response fields to report, the existing-request notice, the two source failures. Inside a study workspace with no identifier named, the paper is the record in `.exactory/deposit.json` (`doi` of the production record). It drops: the constitution and workflow preamble, `status --summary`, `next --summary`, `gate deposited`, `reconcile` before a write, and the closing paragraph on intents.

`skills/verify/SKILL.md` keeps: the product paragraph, the API-key handling, the security rule, the independence rule, getting a task (including opening the verification with `exactory submit` when the paper has none), reading the paper in full, the five judgment checks with `exactory-check lookup --refs-json` and `exactory-derive check`, freezing the cohort with `exactory-cohort freeze`, the verdict file shape and the server's rules, voting, and the report. Step 2 reads: open `url`, read the whole paper with its figures and tables, then research its context. Step 4 sends `exactory verify <id> --file verdict.json`. It drops: the constitution and workflow preamble, the verification profile, acquisition and store initialization, cohort enumeration, tier readings, the five search purposes, `gate preparation`, `task --bind`, `bind-verdict`, `--expected-revision`, `--request-id`, and the paragraph on pending intents.

`skills/deposit/SKILL.md` keeps: the product paragraph, the token rules, parking on a missing token, the citation lookup step, the abstract file step, the production deposit command, the disclosure read-back, the new-version section, the decision log and state lines for a study. It changes: the sandbox deposit becomes a step the agent takes only when the user asks for a test record, so the procedure goes from the abstract file to the production command. The citation lookup step says that the agent fixes blocking findings at the reference and, when the user has asked for the deposit now, deposits now and reports the findings beside the DOI. "Before anything else" says that the user's instruction to deposit is the authorization, that the deposit runs at the time the user names, and that parking before production happens only when the user named that stop. It drops: the constitution and workflow preamble, `status --summary`, `next --summary`, `gate publication`, `gate round`, the bundle and review sentences in steps 2 and the new-version section, and `reconcile` before a write.

`skills/evaluate/SKILL.md`, `skills/ai-science/SKILL.md`, and `skills/write/SKILL.md` are unchanged. Their references to deposit and submit describe the managed receipts a study writes, which section 1 preserves.

## 7. Documentation

- `README.md`: the Install paragraph names 0.40.0 and what it changes; the "Citation gate and hooks" section becomes "Citation report", describing the report that `exactory submit` and `exactory-draft deposit --production` print and the advisory `.bib` hook; the "What runs without a key" paragraph and the AI Science paragraph keep their statements about managed deposit and submission.
- `docs/research-workflow.md`: the last paragraph of "Manuscript assessment and publication" and the "Verification and native mathematics" section state that the deposit, submit, and verify commands run on the user's instruction and write the managed receipt when the current gates pass.
- `docs/research-cli.md`: the `exactory-draft deposit`, managed `exactory submit`, and verification paragraphs state the same rule; the `bind-task` and `bind-verdict` flow stays documented as the way to obtain a verification receipt.
- `codex/README.md`: the sentence on deposit prerequisites states the same rule.
- `docs/releases/0.40.0.md`: the release note, in the style of `0.39.2.md`.
- `docs/testing/user-authorized-publication.md`: the test record for this release, in the style of `round-integrity.md`.

## 8. Tests

Deleted: `TestCitationGate` and `TestPredictionGate` in `tests/test_hooks.py`; `test_bash_still_reaches_shared_submission_gate` in `tests/test_codex.py`; the `enforce_citation_check.py` assertions in `test_each_hook_is_wired_to_its_designed_event_and_matcher`.

Changed: `TestSubmitCitationGate` in `tests/test_transport.py` becomes `TestSubmitDecision` (a failing gate writes the report and the submit proceeds); `TestProductionDepositGate` in `tests/test_draft.py` becomes `TestProductionDepositCitationReport`; `test_deposit_outside_a_workspace_points_at_init` stays (a deposit needs `.exactory/draft.json` for its title). Three tests assert the refusals this release removes and change to assert the note and the direct deposit: `test_first_deposit_requires_an_approved_stop_before_remote_writes` and `test_a_new_version_requires_its_own_stop_and_keeps_the_prior_record` in `tests/test_research_round_integrity.py`, and `test_bare_draft_marker_is_not_publication_readiness` in `tests/test_research_gates.py`. The preview tests in `tests/test_research_round_integrity.py` stay: a bundle that changes after the managed remote steps started still stops the managed deposit. `test_release_manifests_and_notes_describe_the_same_final_version` in `tests/test_research_guidance.py` names 0.40.0.

Added, one per behavior of section 1:

- `exactory verify` from a directory with no workspace posts the verdict and prints the response.
- `exactory verify` in a verification workspace with a bound matching assessment takes the managed path and writes the verdict receipt.
- `exactory verify` in a workspace whose bound assessment does not match the body writes the `Managed record skipped` line and posts the verdict.
- `exactory submit` in a draft workspace with no store posts the request.
- `exactory submit` in a study whose publication gate is ready writes the submission receipt.
- `exactory submit` in a study whose publication gate is not ready writes the skipped line and posts the request.
- `exactory-draft deposit` in a draft workspace with no store, and in a study whose gates are not ready, runs the direct flow, uploads the PDF, and writes `.exactory/deposit.json`.
- `exactory-draft deposit` in a ready study writes the publication receipt.
- A direct deposit in a workspace with a store records the workspace deposit record, and a later projection export keeps `.exactory/deposit.json` unchanged, including after a managed first version.
- The decision point notes a corrupt store and takes the direct path.
- `exactory-draft deposit --new-version` on the direct path reuses the deposition in `.exactory/deposit.json` and refuses an environment mismatch.
- `hooks/hooks.json` and `codex/hooks.json` wire exactly the remaining scripts.

## 9. Release

Version `0.40.0` in `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`. Branch `feat/user-authorized-publication`, one pull request into `main`, the six CI jobs, an annotated tag `exactory--v0.40.0`, and a GitHub release whose body is the release note with absolute links. The user confirms the push before it happens.

## Out of scope

- The AI Science stage machine and its gates.
- The evaluate, ai-science, write, cohort, literature-review, ideate, experiment, and math-solver skills.
- `RESEARCH_CONSTITUTION.md`.
- The server.
- Reconciliation for an interrupted direct deposit. An interrupted direct deposit leaves a draft deposition on Zenodo that the user deletes by hand; a published record is created only by the final publish step.
