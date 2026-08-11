---
description: Read a verification's status and result on exactory. Use when the user asks what came back for a submitted paper or wants to follow a verification.
---

# Read a verification's status

The `exactory` command is on PATH while this plugin is enabled. It prints JSON on
success. It prints an error message on stderr and exits non-zero on failure.

If `EXACTORY_API_KEY` is not set, stop and tell the user: create an API key at
https://www.exactory.ai/console, then export it as `EXACTORY_API_KEY`. Do not ask the
user to paste the key into the chat.

## Procedure

1. Run `exactory status <verification-id>`. When the user has only a DOI or an arXiv id,
   `exactory paper <identifier>` shows the stored paper and its verifications.
2. Report the status. When claims are present, summarize per claim: `dimension`,
   `procedure`, `severity`, `status`, `verdict`. When `background`, `rationale`, or
   `plan` is present, report it in that order: `background` is the context around the
   finding, `rationale` is the core statement, and `plan` is the suggested fix. A
   claim with a `targetClaimId` disputes another claim; say so.
3. `paper` is null until ingest reads the paper from its source. Tell the user to check
   again later when it is null.

## Notes

- The requester's identity is never shown to verifiers, and verifier output reaches the
  requester through this status call.
- Do not poll in a loop. Check once, report, and let the user decide when to check again.
