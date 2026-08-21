---
description: Deposit a paper's preprint to Zenodo and get its DOI, from a draft workspace on exactory. Use when a draft is ready to become a citable record, or to publish a revised version of one already deposited.
---

# Deposit to Zenodo

The product of this stage is a citable, immutable record: the preprint on
Zenodo with a DOI, deposited by the human author who takes responsibility for
it. A submitted paper is verified on this fixed record, so deposit is the border
between the inner loop (which you can re-run) and the outer loop (which runs
against something that no longer moves).

Run every command from the workspace root, the directory that holds
`.exactory/draft.json`. The tools are `exactory-draft` and `exactory-check`, on
PATH while this plugin is enabled.

## Before anything else

- Production publishing is permanent. It runs only with the user's explicit
  approval, given live or pre-authorized in the invocation ("run it all the way
  through submit"). Under autopilot without pre-authorization, prepare
  everything and park before production
  (`exactory-lab state set --waiting production-deposit`).
- The Zenodo tokens are exported by the user, never pasted into chat. Sandbox
  uses `ZENODO_SANDBOX_TOKEN`, production uses `ZENODO_TOKEN`. Run
  `exactory-lab keys` to read which one is set.
- A missing token ends this stage, never the study. The paper is already
  finished at this point, so park the run instead of failing it:
  `exactory-lab state set --waiting zenodo-token`. Then tell the user three
  things: the paper is complete in the workspace, nothing was sent anywhere, and
  the exact variable to export to continue. `exactory-lab keys` prints where to
  create the token. Do not ask the user to paste the token into the chat.

## Procedure

1. **Confirm the citations are clean.** Run `exactory-check lookup` and check
   the report. Production deposit runs the citation gate itself; run the check
   now instead of discovering it at the gate. Fix any blocking finding at the
   reference, never in the report.
2. **Deposit to the sandbox first.**
   ```
   exactory-draft deposit --creator "<Family, Given>"
   ```
   Repeat `--creator` for more authors. Sandbox and draft are the defaults. The
   deposit metadata carries an AI-assistance disclosure naming the human as the
   responsible author. Present the sandbox record to the user and tell them
   about the disclosure. The record is written to `.exactory/deposit.json`.
3. **Deposit and publish to production**, with approval. Present the exact
   command first:
   ```
   exactory-draft deposit --production --publish --confirm-publish --creator "<Family, Given>"
   ```
   Wait for the user's approval (or proceed on a pre-authorizing invocation),
   then run it. Report the record DOI and the concept DOI; the concept DOI names
   the paper across all its versions and is the one to submit.

## Publishing a revised version

When the paper has already been deposited and the improvement loop produced a
better version, deposit a new version instead of a fresh record:

```
exactory-draft deposit --production --publish --confirm-publish --new-version --creator "<Family, Given>"
```

`--new-version` opens a new version of the deposit recorded in
`.exactory/deposit.json`, on the same environment. The concept DOI stays the
same; a new version DOI is minted. The first version keeps its DOI and its
place on the record.

Log the stage decision (the DOI, whether sandbox or production) and, when the
user is taking it to the market, set the state:
`exactory-lab state set --stage submit --status pending`.

## What not to do

- Do not deposit to production or publish without the user's approval or a
  pre-authorizing invocation.
- Do not treat a missing Zenodo token as a study failure. Park the run and
  report the finished local paper.
- Do not paste a Zenodo token into the chat; the user exports it.
- Do not edit the citation report to pass the gate; fix the references.
- Do not hand-write the deposit metadata; `exactory-draft` builds it.
