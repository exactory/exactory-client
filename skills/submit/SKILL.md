---
description: Submit a paper to exactory for verification. Use when the user wants a paper verified and gives an arXiv id, a DOI, or a record page URL.
---

# Submit a paper

The `exactory` command is on PATH while this plugin is enabled. It prints JSON on
success. It prints an error message on stderr and exits non-zero on failure.

If `EXACTORY_API_KEY` is not set, stop and tell the user: create an API key at
https://www.exactory.ai/console, then export it as `EXACTORY_API_KEY`. Do not ask the
user to paste the key into the chat.

## Procedure

exactory accepts papers from arXiv and Zenodo. Send the identifier the user gives you.

1. Run the command that matches what the user has:
   - an arXiv id: `exactory submit --arxiv-id 2301.00001`
   - a DOI from either source: `exactory submit --doi 10.5281/zenodo.21381192`
   - a record page URL: `exactory submit --url https://zenodo.org/records/21381192`

   When the study adopted a Grand Challenge during ideation (the id is recorded
   in `idea/idea.md`), declare it: add `--challenge <challenge-id>` for each
   adopted challenge, at most 5.
2. Report the `id`, `status`, `doi`, and `webUrl` fields to the user.
3. Tell the user that `/exactory:status <id>` reads what comes back.

The `doi` in the response names the paper across its versions, so it differs from a
Zenodo version DOI that was sent. Report the DOI that came back.

On a repeat submit of the same paper, the command prints "The server returned the
existing open request for this paper." Relay that line to the user.

Two failures need a different next step from the user:

- The command reports that the source has no such record. Ask the user to check the
  identifier.
- The command reports that the source is not reachable. Send the same request later.
