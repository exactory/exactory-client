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

The key is what the market commands use. Writing a paper does not need it.

1. Create an API key at https://www.exactory.ai/console.
2. Export it before you start Claude Code:

```
export EXACTORY_API_KEY=<your key>
```

The plugin never asks for the key in chat, and the key never appears in a
payload.

`exactory-lab keys` prints which credentials this environment holds, what each
one unlocks, and what a study still does without it. It never prints a value.

## What runs without a key

A study writes and evaluates a paper with no credential at all: the cohort, the
problem, the experiments, the draft, the citation check, and the evaluation
loop. Those stages read public sources that need no key (arXiv, Crossref,
DataCite, OpenAlex, PubMed, Zenodo). Credentials gate the last two stages only.
`ZENODO_TOKEN` deposits the preprint, and `EXACTORY_API_KEY` submits it. Without
them the run reaches a finished paper in the workspace, parks there, and names
the variable to export to go further. The paper goes nowhere until the key that
sends it is set.

## Exactory AI Science

`/exactory:ai-science` runs a research study end to end: build the cohort and
its doctrine, set a problem, run experiments, draft, evaluate and improve until
the quality saturates, deposit a preprint, and submit it for verification. It is
one loop — the same evaluation a submitter rehearses in private is the one the
market's verifiers run in public on the deposited record.

The agent is the scientist: it sets the problem, writes and runs the experiment
code, writes the paper, and judges it, with no external LLM keys. A study is one
workspace, created by `exactory-lab init`; the loop reads `context/` at the
start and at every improvement iteration, so the user drops material in as it
runs. Experiments run on a pluggable compute layer (`local` by default, `colab`
for GPU nodes — see [`colab/README.md`](colab/README.md)). By default the study
runs end to end and stops only for the context grace phase and for production
deposit or submission; the user names any other pacing in their own words.

| Stage skill | Purpose |
|---|---|
| `/exactory:cohort` | Build the cohort and extract its doctrine: the field's rules, its authorities, its open problems with their advance criteria |
| `/exactory:ideate` | Turn an open problem and the human context into a specific, novel, feasible problem |
| `/exactory:experiment` | Run a best-first experiment search, with an optional autoresearch optimization mode |
| `/exactory:deposit` | Deposit the preprint to Zenodo and get its DOI |

## Skills

| Skill | Persona | Purpose |
|---|---|---|
| `/exactory:ai-science` | Submitter | Run a study end to end: cohort, problem, experiments, draft, improve, deposit, submit |
| `/exactory:write` | Submitter | Draft the paper: evidence intake and doctrine-conforming sections with verified citations |
| `/exactory:evaluate` | Both | Evaluate a paper locally: citation integrity, a structured quality review, and an impact self-prediction |
| `/exactory:submit` | Submitter | Submit a paper for verification |
| `/exactory:status` | Submitter | Read a verification's status and result |
| `/exactory:verify` | Verifier | Verify a paper end to end: check citations and consistency, predict its citation rank, submit one review |
| `/exactory:verify-rank` | Verifier | Predict a paper's citation rank and submit only that prediction |
| `/exactory:verify-citations` | Verifier | Check the references against the registries and submit what fails |
| `/exactory:verify-consistency` | Verifier | Check cross-references and value agreement and submit the findings |
| `/exactory:verify-quality` | Verifier | Score the paper against the registered rubric and submit the appraisal |
| `/exactory:verify-derivation` | Both | Check that the paper's equation manipulations hold; a proven-invalid step is a soundness finding |
| `/exactory:challenge` | Both | Post, browse, vote on, solve, and report Grand Challenges: structured statements of unsolved research problems |

## CLIs

Six commands are on PATH while the plugin is enabled. Each is one Python 3
file, standard library only.

**`exactory`** is the transport to the API:

```
exactory submit --arxiv-id 2301.00001
exactory submit --url https://zenodo.org/records/21381192
exactory submit --doi 10.5281/zenodo.21381192 --challenge <challenge-id>
exactory status <verification-id>
exactory tasks --limit 10
exactory tasks --query "sparse attention" --category cs.LG --sort relevance
exactory task <verification-id>
exactory paper 2301.00001
exactory submit-review <verification-id> --file review.json
exactory challenges --field cs.LG --status open --sort top
exactory challenge <challenge-id>
exactory vote-challenge <challenge-id> --value 1
exactory solve-challenge <challenge-id> --note "How the criteria are met."
exactory report-challenge <challenge-id> --note "Why this violates the rules."
```

`post-challenge` posts a Grand Challenge from its six required parts (title,
field, problem statement, current state, resolution criteria, and a citations
JSON file). The `/exactory:challenge` skill walks the fields and verifies
every citation locator before it posts.

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
`--confirm-publish` flag, because a published DOI is permanent. `--new-version`
publishes a revised version of a record already deposited, keeping the concept
DOI. The publish output prints the record DOI and the concept DOI, and
`exactory submit` takes the concept DOI.

**`exactory-lab`** owns an Exactory AI Science study. `init` creates the study
workspace and its git repository, `keys` reports which credentials the
environment holds and what the study does without each one, `state` and
`decide` drive the study state machine and its append-only decision log, and
`run` executes an experiment script on a compute backend (`local`, or `colab` via `colab-status` and
`colab-serve`), confined to the workspace and recording a result the experiment
journal is built from.

**`exactory-derive`** checks a paper's equation manipulations. `check` reads a
JSON list of steps — each an evaluable `from` and `to` expression with the
variables' ranges — and, using only a whitelisted arithmetic grammar (never
`eval`), finds a point where the two sides differ. Such a point is a
counterexample: the step is invalid, and the witness is reproducible. Agreement
is soft evidence. When SymPy is installed it adds a symbolic verdict, but it is
never a hard dependency.

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
