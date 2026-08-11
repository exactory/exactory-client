---
description: Post or read discussion on a paper on exactory. Use when the user wants to comment on a paper, reply in its discussion, or read what others said.
---

# Discuss a paper

Discussions are public commentary on a paper. They are never adjudicated and carry no
stake; a claim someone can settle belongs in a review (`/exactory:verify`), not here.

If `EXACTORY_API_KEY` is not set, stop and tell the user: create an API key at
https://www.exactory.ai/console, then export it as `EXACTORY_API_KEY`.

## Read a discussion

```
exactory discussions <doi-or-arxiv-id> --limit 50
```

Report each entry with its author, body, and time. An entry with a `parentId` is a
reply; an entry with a `targetClaimId` is anchored to that claim.

## Post

A discussion post is public. Post text the user gives you as it is. If the user asks you
to draft the comment, show the draft and post it only after the user approves.

```
exactory discuss <doi-or-arxiv-id> --body "The comment text"
```

- A long comment travels better as a file: `--body-file comment.txt`.
- To anchor the comment to one claim, pass `--claim-id <claim-id>`.
- To reply under an existing entry, pass `--parent-id <discussion-id>`.

The identifier is the paper's DOI or arXiv id, not the verification id; the discussion
follows the paper across its versions.
