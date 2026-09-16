---
description: Submit a paper to exactory for verification. Use when the user wants a paper verified and gives an arXiv id, a DOI, or a record page URL.
---

# Submit a paper

The `exactory` command is on PATH while this plugin is enabled. It prints JSON on
success. It prints an error message on stderr and exits non-zero on failure.

The command runs from any directory, and the user's request is its authorization.
A managed study works under the [research constitution](../../RESEARCH_CONSTITUTION.md);
inside a study whose publication gate is ready, the CLI also records the submission
receipt that closes the study's `submit` stage. In any other workspace it prints one
line, `Managed record skipped (<code>): <message>`, and sends the request anyway.
Inside a draft workspace it first prints the offline citation report; a failing
report is information for the user, and the submit continues.

If the command reports that no API key is found, the next step depends on where you are.

- Inside a study workspace (a directory tree that holds `.exactory/study.json`), a
  missing key ends this stage, never the study. Park the run:
  `exactory-lab state set --waiting exactory-api-key`. Then tell the user that the
  paper is deposited and citable, that nothing went to exactory, and that
  `/exactory:login` continues from here.
- Anywhere else, offer `/exactory:init`, which sets up a key in the session or through the
  web sign-up page. A key created at https://www.exactory.ai/keys and exported as
  `EXACTORY_API_KEY` also works.

Do not ask the user to paste the key into the chat.

## Procedure

exactory accepts papers from arXiv and Zenodo.

1. Find the identifier to send.
   - The user named a paper: use that identifier.
   - The user named none and you are in a study workspace: the paper is the one this
     study deposited. Read `.exactory/deposit.json` and take `doi`, the record's DOI.
     That file also carries `environment`. A `sandbox` record is a test deposit the
     server cannot fetch; when the record is a sandbox one, tell the user and deposit
     to production first.
2. Run the command that matches the identifier:
   - an arXiv id: `exactory submit --arxiv-id 2301.00001`
   - a DOI from either source: `exactory submit --doi 10.5281/zenodo.21381192`
   - a record page URL: `exactory submit --url https://zenodo.org/records/21381192`

   When the study adopted a Grand Challenge during ideation (the id is recorded
   in `idea/idea.md`), declare it: add `--challenge <challenge-id>` for each
   adopted Grand Challenge, at most 3.
3. Report the `id`, `status`, `doi`, and `webUrl` fields to the user.
4. Tell the user that `/exactory:status <id>` reads what comes back.

The `doi` in the response names the paper across its versions, so it differs from a
Zenodo version DOI that was sent. Report the DOI that came back.

A paper carries one verification, whoever asks for it. So a submit of a paper somebody
already submitted returns that standing request instead of opening a second one, and the
command prints "The server returned the existing open request for this paper." Relay that
line to the user. `/exactory:status` reads a request this account opened, so when the
standing request belongs to another account, point the user at `webUrl` instead.

Two failures need a different next step from the user:

- The command reports that the source has no such record. Ask the user to check the
  identifier.
- The command reports that the source is not reachable. Send the same request later.
