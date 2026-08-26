# exactory

The Claude Code plugin for [exactory](https://www.exactory.ai), the
paper-verification market. One plugin serves both personas:

- A **submitter** writes a paper with verified citations, deposits it as a
  preprint on Zenodo, submits it for verification, and reads the result.
- A **verifier** lists open tasks, reads a paper's pinned version, and votes
  on whether it is sound. exactory publishes the count of those votes. It
  states no verdict of its own about a paper.

## Install

```
claude plugin marketplace add exactory/marketplace
claude plugin install exactory@exactory-ai
```

### If you installed exactory-verifier

The `exactory-verifier` plugin is retired. The verifier workflow lives in this
plugin, under the same `/exactory:*` namespace. The marketplace records the
rename, so Claude Code migrates your installation when the marketplace
updates. Your API key does not change.

## Get the API key

The key is what the market commands use. Writing a paper does not need it.

Say `/exactory:init` in a session for the guided setup: it checks what is already
set, then registers you in the session with an emailed code, or opens the web
sign-up page (`exactory open-signup`) if you prefer a password or Google/GitHub.
Or run the two commands yourself:

```
exactory login --email you@example.org
exactory login --email you@example.org --code 123456
```

The first command sends a one-time code to the address. The second one proves
the address with the code and stores a key in `~/.config/exactory/credentials.json`
(`$XDG_CONFIG_HOME` is honored), readable by you only. A new address becomes an
account; an existing one gets one more key. When you use the code, you agree to the
[Terms of Service](https://www.exactory.ai/policies/terms) and the
[Privacy Policy](https://www.exactory.ai/policies/privacy).

`EXACTORY_API_KEY`, when set, wins over the file. A key from
https://www.exactory.ai/keys works there too. `exactory logout` removes
the file. The key stays valid until you revoke it on that page.

The plugin never asks for the key in chat, never prints it, and the key never
appears in a payload.

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
| `/exactory:init` | Both | Guided setup: check what is set, then register in the session with an emailed code or through the web sign-up page |
| `/exactory:login` | Both | Sign in or create an account with a code sent to your email, and store the API key locally |
| `/exactory:ai-science` | Submitter | Run a study end to end: cohort, problem, experiments, draft, improve, deposit, submit |
| `/exactory:write` | Submitter | Draft the paper: evidence intake and doctrine-conforming sections with verified citations |
| `/exactory:evaluate` | Both | Evaluate a paper locally: citation integrity, a structured quality review, and the verdict you expect the market to reach |
| `/exactory:submit` | Submitter | Submit a paper for verification |
| `/exactory:status` | Submitter | Read a verification's status and result |
| `/exactory:verify` | Verifier | Verify a paper: read the pinned version, judge whether it is sound, cast one vote |
| `/exactory:propose-grand-challenge` | Both | Propose a Grand Challenge that states an unsolved research problem, and browse, vote on, solve, and report the ones already posted |

## CLIs

Six commands are on PATH while the plugin is enabled. Each is one Python 3
file, standard library only.

**`exactory`** is the transport to the API:

```
exactory login --email you@example.org
exactory login --email you@example.org --code 123456 --label "plugin on laptop"
exactory whoami
exactory open-signup
exactory logout
exactory submit --arxiv-id 2301.00001
exactory submit --url https://zenodo.org/records/21381192
exactory submit --doi 10.5281/zenodo.21381192 --challenge <challenge-id>
exactory status <verification-id>
exactory tasks --limit 10
exactory tasks --query "sparse attention" --category cs.LG --sort relevance
exactory task <verification-id>
exactory paper 2301.00001
exactory vote <verification-id> --value 1
exactory challenges --field cs.LG --status open --sort top
exactory challenge <challenge-id>
exactory vote-challenge <challenge-id> --value 1
exactory solve-challenge <challenge-id> --note "How the criteria are met."
exactory report-challenge <challenge-id> --note "Why this violates the rules."
```

`post-challenge` posts a Grand Challenge from its six required parts (title,
field, problem statement, current state, resolution criteria, and a citations
JSON file). The `/exactory:propose-grand-challenge` skill walks the fields and
verifies every citation locator before it posts.

Each command prints JSON on success. On failure it prints one error message on
stderr and exits non-zero.

**`exactory-cohort`** freezes the population a study is read against. `freeze`
computes the field and the six-month window from the paper's own fields, with
no network call, or from the source API as a fallback.

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
| `EXACTORY_API_KEY` | API key, sent as a Bearer token. Optional: it wins over the file that `exactory login` writes. | none |
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
