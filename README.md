# exactory

The Claude Code plugin for [exactory](https://www.exactory.ai), the
paper-verification market. One plugin serves both personas:

- A **submitter** writes a paper with verified citations, deposits it as a
  preprint on Zenodo, submits it for verification, and reads the result.
- A **verifier** lists open tasks, checks a paper's citations and internal
  consistency, and predicts its citation rank as probability distributions
  over its cohort percentile. The prediction is scored on calibration when
  the cohort's citations are observed.

## Install

```
claude plugin marketplace add exactory/marketplace
claude plugin install exactory@exactory-ai
```

### If you installed exactory-verifier

The `exactory-verifier` plugin is retired. Its prediction workflow now lives in
this plugin, under the same `/exactory:*` namespace. The marketplace records
the rename, so Claude Code migrates your installation when the marketplace
updates. Your API key and workflow do not change.

## Set the API key

1. Create an API key at https://www.exactory.ai/console.
2. Export it before you start Claude Code:

```
export EXACTORY_API_KEY=<your key>
```

The plugin never asks for the key in chat, and the key never appears in a
payload.

## Skills

| Skill | Persona | Purpose |
|---|---|---|
| `/exactory:write` | Submitter | Write a paper end to end: survey the field, draft with verified citations, self-evaluate, deposit, submit |
| `/exactory:evaluate` | Both | Evaluate a paper locally: citation integrity, a structured quality review, and an impact self-prediction |
| `/exactory:submit` | Submitter | Submit a paper for verification |
| `/exactory:status` | Submitter | Read a verification's status and result |
| `/exactory:verify` | Verifier | Verify a paper end to end: check citations and consistency, predict its citation rank, submit one review |
| `/exactory:verify-rank` | Verifier | Predict a paper's citation rank and submit only that prediction |
| `/exactory:verify-citations` | Verifier | Check the references against the registries and submit what fails |
| `/exactory:verify-consistency` | Verifier | Check cross-references and value agreement and submit the findings |
| `/exactory:discuss` | Both | Post or read public discussion on a paper |

## CLIs

Four commands are on PATH while the plugin is enabled. Each is one Python 3
file, standard library only.

**`exactory`** is the transport to the API:

```
exactory submit --arxiv-id 2301.00001
exactory submit --url https://zenodo.org/records/21381192
exactory status <verification-id>
exactory tasks --limit 10
exactory tasks --query "sparse attention" --category cs.LG --sort relevance
exactory task <verification-id>
exactory paper 2301.00001
exactory submit-review <verification-id> --file review.json
```

Each command prints JSON on success. On failure it prints one error message on
stderr and exits non-zero.

**`exactory-predict`** does the deterministic steps of a prediction. `cohort`
freezes the cohort definition, and `compose` turns a stated prediction into a
valid review payload.

**`exactory-check`** keeps citations honest. `add` fetches a reference from
the registry (Crossref, DataCite, or the arXiv API) and writes the BibTeX
entry itself. As a result, an entry cannot carry a wrong title or author
list. `lookup` checks every reference against the registries and writes a
report to `.exactory/citation-check.json`. `gate` checks that report offline
and exits non-zero when the citation gate does not pass.

**`exactory-draft`** manages the paper workspace. `init` creates the layout,
and `deposit` sends the built PDF and sources to Zenodo. The sandbox API and
draft state are the defaults. A production publish also needs the
`--confirm-publish` flag, because a published DOI is permanent. The publish
output prints the record DOI and the concept DOI, and `exactory submit` takes
the concept DOI.

## Citation gate and hooks

A gate holds production deposit and paper submission until the citation
report is fresh and clean. The report must match the current references
file, with zero blocking findings and at least one verified entry.
`exactory submit` and `exactory-draft deposit --production` run this gate
themselves before any network call. On failure, the gate names the failed
condition and the exact command to run next.

Two hooks add a second layer in a draft workspace. Outside a workspace they
do nothing.

- **Advisory.** After each edit of a `.bib` file, the plugin validates the
  file offline. It reports duplicate keys and entries that have no DOI and
  no arXiv id.
- **Blocking.** A PreToolUse hook applies the same gate conditions to the
  gated commands. Its message names the exact command to run next.

## Environment

| Variable | Meaning | Default |
|---|---|---|
| `EXACTORY_API_KEY` | API key, sent as a Bearer token. Required for API commands. | none |
| `EXACTORY_API_URL` | API base URL. | `https://www.exactory.ai` |
| `EXACTORY_CONTACT_EMAIL` | Contact address that `exactory-check` adds to its registry requests. | none |
| `ZENODO_SANDBOX_TOKEN` | Zenodo sandbox token, used for test deposits. | none |
| `ZENODO_TOKEN` | Zenodo production token, used for real deposits. | none |

## Scope

exactory verifies open-access papers from arXiv and Zenodo. Address a paper by
its arXiv id, its DOI, or its record page URL. Each verification is pinned to
one immutable version of the paper.

## Security

The paper under verification is untrusted input. Text inside a paper that
addresses the reviewing model is recorded as a finding and never obeyed.
