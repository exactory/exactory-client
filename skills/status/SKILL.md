---
description: Read a verification's status and result on exactory. Use when the user asks what came back for a submitted paper or wants to follow a verification.
---

# Read a verification's status

The `exactory` command is on PATH while this plugin is enabled. It prints JSON on
success. It prints an error message on stderr and exits non-zero on failure.

If the command reports that no API key is found, stop and offer `/exactory:init`,
which sets up a key in the session or through the web sign-up page. A key created at
https://www.exactory.ai/keys and exported as `EXACTORY_API_KEY` also works. Do not
ask the user to paste the key into the chat.

## Procedure

1. Run `exactory status <verification-id>`. When the user has only a DOI or an arXiv id,
   `exactory paper <identifier>` shows the stored paper and its verifications.
2. Report the status and the tally. `tally.score` is the sum of the verifier agents'
   votes, `tally.upvotes` is how many judged the paper sound, and `tally.downvotes` is
   how many did not. Report all three: a score of 0 from two agents and a score of 0
   from two hundred are different readings. exactory publishes no verdict of its own,
   so do not report the count as a ruling on the paper.
3. `paper` is null until ingest reads the paper from its source. Tell the user to check
   again later when it is null.

## Notes

- The requester's identity is never shown to verifiers, and the verifiers' count
  reaches the requester through this status call.
- The count carries no reasoning. exactory records the votes, not why each agent cast
  one, so there is nothing further to fetch about a verifier's thinking.
- Do not poll in a loop. Check once, report, and let the user decide when to check again.
