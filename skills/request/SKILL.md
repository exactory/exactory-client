---
description: Submit a paper to exactory for verification, check a verification's status, or read its result. Use when the user wants a paper verified, names exactory, or asks what came back for a submitted paper.
---

# Request a verification

The `exactory` command is on PATH while this plugin is enabled. It prints JSON on
success. It prints an error message on stderr and exits non-zero on failure.

If `EXACTORY_API_KEY` is not set, stop and tell the user: create an API key at
https://www.exactory.ai/console, then export it as `EXACTORY_API_KEY`. Do not ask the
user to paste the key into the chat.

## Submit a paper

exactory accepts papers from arXiv and Zenodo. Send the identifier the user gives you.

1. Run the command that matches what the user has:
   - an arXiv id: `exactory submit --arxiv-id 2301.00001`
   - a DOI from either source: `exactory submit --doi 10.5281/zenodo.21381192`
   - a record page URL: `exactory submit --url https://zenodo.org/records/21381192`
2. Report the `id`, `status`, `doi`, and `webUrl` fields to the user.

The `doi` in the response names the paper across its versions, so it differs from a
Zenodo version DOI that was sent. Report the DOI that came back.

On a repeat submit of the same paper, the command prints "The server returned the
existing open request for this paper." Relay that line to the user.

Two failures need a different next step from the user:

- The command reports that the source has no such record. Ask the user to check the
  identifier.
- The command reports that the source is not reachable. Send the same request later.

## Check a verification

1. Run `exactory status <verification-id>`.
2. Report the status. When claims are present, summarize per claim: `dimension`,
   `procedure`, `severity`, `status`, `verdict`. A claim with a `targetClaimId`
   disputes another claim; say so.
3. `paper` is null until ingest reads the paper from its source. Tell the user to check
   again later when it is null.

## Notes

- The requester's identity is never shown to verifiers, and verifier output reaches the
  requester through this status call.
- Do not poll in a loop. Check once, report, and let the user decide when to check again.
