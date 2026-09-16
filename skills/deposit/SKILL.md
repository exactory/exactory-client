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

- The user's instruction to deposit is the authorization, and the deposit runs at
  the time the user names. Production publishing is permanent; the
  `--confirm-publish` flag records that the user asked for it. Park before
  production (`exactory-lab state set --waiting production-deposit`) only when
  the user named that stop ("prepare the deposit but let me publish it").
- The command runs in any draft workspace. In a study that works under the
  [research constitution](../../RESEARCH_CONSTITUTION.md) and whose publication
  and round gates are ready, the CLI records the publication receipt that closes
  the study's `deposit` stage. A workspace that refuses the receipt prints one
  line, `Managed record skipped (<code>): <message>`, and creates the record
  anyway. A draft workspace with no store, such as one initialized before
  0.38.0, creates it with no line.
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

1. **Read the citation report.** Run `exactory-check lookup` and read the report.
   Fix any blocking finding at the reference, never in the report. A production
   deposit runs the same check again and prints its report on stderr as
   `Citation report:` when it fails, then continues. When the user has asked for
   the deposit now, deposit now and report the blocking findings beside the DOI.
2. **Write the abstract to a file.** Copy the paper's final abstract into
   `draft/abstract.txt` as plain text: no LaTeX commands, paragraphs separated
   by one blank line. This file becomes the record's description on Zenodo,
   so it must match the abstract in the PDF word for word.
3. **Deposit and publish to production.**
   ```
   exactory-draft deposit --production --publish --confirm-publish --creator "<Family, Given>" --abstract-file draft/abstract.txt
   ```
   Repeat `--creator` for more authors. The record's description opens with the
   abstract and closes with a disclosure naming the human as the responsible
   author. There are two disclosures, and the command picks between them: it
   names exactory.ai as the paper's writer when the PDF comes from this
   workspace's `draft/` tree and `.exactory/authorship.json` reads
   `written_by_exactory` true, which the plugin writes when an agent writes a
   LaTeX source under `draft/`, and otherwise it states only that the paper was
   prepared with AI assistance and deposited through exactory.ai. The PDF is
   uploaded as `paper.pdf` and listed first; the sources archive follows it.
   State the command in the report beside its result: the record DOI and the
   concept DOI. The concept DOI names the paper across all its versions;
   `/exactory:submit` reads the record DOI from `.exactory/deposit.json`. Read
   the disclosure back to the user from the record, never from memory. When the
   user named a stop before production, park instead and hand them the exact
   command.
4. **A test record, when the user asks for one.** The same command without
   `--production` (and without `--confirm-publish`) creates the record on the
   Zenodo sandbox with `ZENODO_SANDBOX_TOKEN`. A sandbox record is a rehearsal:
   the server cannot fetch it, so it is never the record to submit.

## Publishing a revised version

When the paper has already been deposited and the improvement loop produced a
better version, deposit a new version instead of a fresh record:

```
exactory-draft deposit --production --publish --confirm-publish --new-version --creator "<Family, Given>" --abstract-file draft/abstract.txt
```

`--new-version` opens a new version of the deposit recorded in
`.exactory/deposit.json`, on the same environment. The concept DOI stays the
same; a new version DOI is minted. The first version keeps its DOI and its
place on the record.

Log the stage decision (the DOI, whether sandbox or production) and, unless
the user ended the run at deposit, set the state:
`exactory-lab state set --stage submit --status pending`.

## What not to do

- Do not stop before production unless the user named that stop; and when they
  did, do not publish until they release it.
- Do not make a sandbox deposit a step of the procedure; it is a test the user
  asks for.
- Do not treat a missing Zenodo token as a study failure. Park the run and
  report the finished local paper.
- Do not run a deposit again when its publish response was lost. The command
  prints the deposition id and its draft URL before it publishes. Open that
  record and read its state first; a second run publishes a second permanent
  record.
- Do not paste a Zenodo token into the chat; the user exports it.
- Do not edit the citation report to pass the gate; fix the references.
- Do not hand-write the deposit metadata; `exactory-draft` builds it.
