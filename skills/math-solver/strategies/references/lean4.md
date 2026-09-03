# Lean 4 reference for the formal check

The toolchain facts behind `../verify-formally-with-lean4.md` and the harness's
`verify lean` command. Every statement about the tools cites the official
page it was read from; the URLs are collected at the end. Read on
2026-09-01, when `latest` in the reference manual served version
4.34.0-rc2 and the stable toolchain was 4.33.1. A version, a flag, or a
command name in this file is a snapshot; the cited page is the authority.

## 1. Install the toolchain

Lean is installed through `elan`, the toolchain manager. The recommended
route on the official install page is the VS Code extension's setup guide;
the manual route, which is the one a harness uses, is the installer script
([install/manual], [elan]):

```sh
curl https://elan.lean-lang.org/elan-init.sh -sSf | sh
```

The elan README says the installer "will tell you where it will install
elan to (`~/.elan` by default), and also ask you about editing your shell
config to extend `PATH`", and that `lake` needs `git` to download
dependencies [elan]. The manual install page adds the step that puts the
proxies on the current shell's PATH: `source $HOME/.elan/env`
[install/manual]. The script itself (read before running) accepts `-y` to
skip the confirmation prompt, `--no-modify-path`, and
`--default-toolchain <name>` [elan-init].

What was run here, non-interactively, without touching the user's shell
files, with the stable channel as the default:

```sh
curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y --no-modify-path --default-toolchain stable
```

Output:

```
info: downloading installer
info: default toolchain set to 'stable'
```

Because the shell files were not modified, every later command was run
with `export PATH="$HOME/.elan/bin:$PATH"`; `~/.elan/env` exports the
same line [install/manual]. The first `lean` invocation downloaded the
toolchain that `stable` resolves to [ref-elan]:

```
$ elan --version
elan 4.2.4 (227caca13 2026-08-25)
$ lean --version
info: downloading https://releases.lean-lang.org/lean4/v4.33.1/lean-4.33.1-darwin_aarch64.tar.zst
info: installing ~/.elan/toolchains/leanprover--lean4---v4.33.1
Lean (version 4.33.1, arm64-apple-darwin24.6.0, commit 819816b2e0a3bf405af45ae5c7af2491d8f5bee6, Release)
$ lake --version
Lake version 5.0.0-src+819816b (Lean version 4.33.1)
$ elan show
leanprover/lean4:v4.33.1 (resolved from default 'stable')
Lean (version 4.33.1, arm64-apple-darwin24.6.0, commit 819816b2e0a3bf405af45ae5c7af2491d8f5bee6, Release)
```

Toolchain selection: the `lean` and `lake` on PATH are proxies that pick
the version named by the nearest `lean-toolchain` file above the working
directory, and fall back to elan's default when there is none; `stable` is
"the latest stable Lean release", and "a project's toolchain file should
typically contain a specific version of Lean, rather than a general
channel" [ref-elan]. Toolchains live in `~/.elan/toolchains`, the proxies
in `~/.elan/bin` [ref-elan].

## 2. Create a project

Every Lean file the harness checks lives in a Lake package: a directory
with a `lakefile.toml` (or `lakefile.lean`) and a `lean-toolchain` file
[install/manual]. `lake new <name> [template]` creates the package in a
new directory; `lake init <name> [template]` creates it in the current
one; the templates are `std` (library and executable, the default), `exe`,
`lib` (library only), and `math` (a library depending on Mathlib)
[ref-lake]. `lake help new` on Lake 5.0.0 also lists `math-lax`. The
configuration file format is TOML unless the template is suffixed
`.lean` [ref-lake].

The smoke fixture at `../../harness/fixtures/lean-smoke/` was created with

```sh
lake init smoke lib
```

(`Main` is refused: `error: reserved package name`.) The template wrote
`lakefile.toml`, `lean-toolchain` (`leanprover/lean4:v4.33.1`, the
toolchain Lake itself belongs to [lake-readme]), `.gitignore` containing
`/.lake`, `Smoke.lean`, `Smoke/Basic.lean`, a `README.md`, and a GitHub
workflow. The fixture keeps `lakefile.toml`, `lean-toolchain`,
`.gitignore`, and `Smoke.lean`, the root module that holds the theorems;
the other generated files were removed. The first
`lake build` added `lake-manifest.json`, which the Lake reference lists as
part of the workspace [ref-lake]; it is committed.

`lakefile.toml` as generated:

```toml
name = "smoke"
version = "0.1.0"
defaultTargets = ["Smoke"]

[[lean_lib]]
name = "Smoke"
```

A library's default facet builds the `.olean` files of its root modules
into `.lake/build/lib` [ref-lake]. A theorem is checked only when its
module is a root of a built library or is imported by one; a file that no
root imports is not compiled [did-you-prove-it].

## 3. Add Mathlib (documented, not run here)

Mathlib was not installed in this session. The official steps, for when
a statement needs it:

New project, from the manual install page and the Mathlib wiki
[install/manual] [mathlib-wiki]:

```sh
lake +leanprover-community/mathlib4:lean-toolchain new <project> math
cd <project>
lake exe cache get
```

The `+leanprover-community/mathlib4:lean-toolchain` prefix makes Lake run
with the toolchain Mathlib currently uses [mathlib-wiki]; the wiki asks
for `elan --version` of 2.0.0 or newer first. `lake exe cache get`
downloads the prebuilt `.olean` files "computed by mathlib4's automated
workflow" [mathlib-readme]; the Lake README says that without it Mathlib
"will be rebuilt from scratch (which can take hours)" [lake-readme], and
the wiki says the command prints a line like `Decompressing 5000 file(s)`
[mathlib-wiki]. The download is multi-gigabyte, which is why it was left
out of this session.

Existing project [mathlib-wiki]: add to `lakefile.toml`

```toml
[[require]]
name = "mathlib"
scope = "leanprover-community"
```

then set the project's toolchain to Mathlib's and fetch the cache:

```sh
curl https://raw.githubusercontent.com/leanprover-community/mathlib4/master/lean-toolchain -o lean-toolchain
lake update
lake exe cache get
```

Pinning: `rev = "v4.15.0-rc1"` (any Mathlib tag) under `[[require]]`
followed by `lake update mathlib` steps to that tag [mathlib-wiki].
Updating later: re-download `lean-toolchain` and run `lake update`, which
also runs `lake exe cache get` [mathlib-wiki] [install/manual]. `lake exe
cache get` is run from the project root; it caches Mathlib and its
upstreams, never the project's own files [mathlib-wiki].

## 4. Build and check axioms

Build [ref-lake]:

```sh
lake build
```

Exit status 0 with `Build completed successfully` means every root module
elaborated and the kernel accepted every declaration in it. `lake build
+Module` on the module holding the theorem, "observing success without
error messages or warnings", gives the same guarantee as the editor's
check marks [validating]. The build alone does not detect `sorry`: a
`sorry` elaborates to an axiom, the build still succeeds with a warning
[validating], and the smoke run below shows exit status 0 in that state.

The axiom check. `#print axioms <name>` "displays all the axioms that a
definition transitively relies on", which "can be used to audit the
assumptions made by a proof, for instance detecting that a proof
transitively depends on the sorry tactic" [ref-axioms]. The harness writes
a scratch file that imports the module and prints the axioms, and runs
`lake env lean` on it; `lake env <cmd>` runs `cmd` with `LEAN_PATH` set to
the workspace's built libraries [ref-lake] [lake-help-env], so the import
resolves against `.lake/build/lib`. The scratch file may live outside the
project; the command is run from the step directory. `lake lean <file>`
(build the file's imports, then run `lean` on it in Lake's environment) is
the documented one-step alternative [ref-lake].

```sh
printf 'import Smoke\n#print axioms square_mod_four\n' > /path/to/scratch.lean
lake env lean /path/to/scratch.lean
```

The module name is the source path relative to the package root with
`.lean` removed and `/` replaced by `.`.

Decision rule on the printed list, from the validating-proofs page
[validating]:

- only `propext`, `Classical.choice`, `Quot.sound`, or a subset, or "does
  not depend on any axioms": the proof is complete relative to Lean's
  standard axioms; the step passes;
- `sorryAx`: "this theorem or one of its dependencies uses sorry or is
  otherwise incomplete"; the step fails;
- `Lean.trustCompiler`, or an axiom named `<decl>._native.native_decide.ax_<k>`
  (see section 5): native evaluation was used; the result is computational
  evidence, not a kernel-checked certificate;
- any other name: a custom axiom was declared, and "the theorem is only
  valid relative to the soundness of these axioms"; the step fails.

### The smoke run, verbatim

Clean fixture (`Smoke.lean` with `square_mod_four` proved by `decide` and
`two_mul_le_add` proved by `omega`; `step.json` names `square_mod_four`):

```
$ lake build
✔ [2/3] Built Smoke (347ms)
Build completed successfully (3 jobs).
$ lake env lean /path/to/check_axioms.lean        # import Smoke / #print axioms square_mod_four
'square_mod_four' does not depend on any axioms
$ lake env lean /path/to/check_axioms_omega.lean  # import Smoke / #print axioms two_mul_le_add
'two_mul_le_add' depends on axioms: [propext, Quot.sound]
```

With a third theorem appended, `theorem unproved_step (n : Nat) : n + 0 =
n := by sorry`:

```
$ lake build
⚠ [2/3] Built Smoke (343ms)
warning: Smoke.lean:17:8: declaration uses `sorry`
Build completed successfully (3 jobs).
$ lake env lean /path/to/check_axioms_sorry.lean  # import Smoke / #print axioms unproved_step
'unproved_step' depends on axioms: [sorryAx]
```

Exit status of both commands was 0. The theorem was then removed and the
clean outputs above were reproduced. A first version of the `decide`
theorem, stated with `(a : Fin 4)` as an argument, failed the build with
`error: Smoke.lean:9:2: Expected type must not contain free variables`
and the hint to use `+revert`; the quantifier was moved into the
statement (section 6).

Beyond `#print axioms`, the reference manual lists two stronger checks:
`lean4checker --fresh` on the module, which replays the stored proofs
through the kernel, and the `comparator` tool with external checkers for
"possibly-malicious" proofs, a class the page says includes "un-reviewed
AI-generated proofs" [validating]. Neither is part of the harness step;
both are what a reviewer runs on a unit before it is published.

## 5. What `sorry`, `native_decide`, and the compiler axioms mean for trust

Lean's kernel is "a small, robust implementation of a type checker for the
core type theory", and every definition is checked by it before it enters
the environment [ref-elab]. Trust in a theorem is trust in the kernel plus
the axioms the proof uses: "any proof that relies on an axiom can be
trusted only to the extent that the axiom is both true and consistent with
the other axioms used" [ref-axioms].

`sorry`. "The axiom sorryAx is used as part of the implementation of the
sorry tactic and sorry term. Uses of this axiom are not intended to occur
in finished proofs, as it can be used to prove anything" [ref-axioms]. A
file with `sorry` builds with a warning; only the axiom list shows it.

`native_decide`. It "is a synonym for decide +native", which evaluates the
`Decidable` instance with compiled code and admits the result "via an
axiom"; the tactic reference says this "adds the entire lean compiler to
the trusted part, and a new axiom will show up in #print axioms for
theorems using this method or anything that transitively depends on them"
[ref-tactics]. The axioms chapter shows the mechanism: "the native_decide
tactic creates a bespoke axiom for each invocation", named like
`bigSum._native.native_decide.ax_1` with type
`decide (...) = true`, "so each axiom can be audited for the precise
statement that it proves" [ref-axioms].

`Lean.ofReduceBool`, `Lean.ofReduceNat`, `Lean.trustCompiler`. These
three axioms "do not truly exist for their mathematical content" but
"track proofs that depend on the correctness of the entire compiler, and
not just on the much smaller kernel" [ref-axioms]; `Lean.ofReduceBool (a
b : Bool) : Lean.reduceBool a = b → a = b` is the axiom that turns a
compiled evaluation into a kernel fact. In the version served as `latest`,
`Lean.reduceNat` carries a deprecation note: "in-kernel native reduction is
deprecated; assert native evaluations with axioms instead" [ref-axioms].
The validating-proofs page states the consequence: native evaluation "can
be used to create invalid proofs whenever the native evaluation of a term
disagrees with the kernel's evaluation", every `implemented_by` or
`extern` replacement in the libraries joins the trusted base, and
"external checkers (lean4checker, comparator) cannot check such proofs";
it also says uses "wrapped in honest tactics (e.g. bv_decide) are
generally trustworthy" because the enlarged trusted base "is still fixed
and vetted" [validating].

For the strategy this means: a theorem whose axiom list is within the
three standard axioms is a certificate. A theorem that depends on
`sorryAx` is not a result. A theorem that depends on a native-evaluation
axiom is computational evidence and is labelled so in the unit; `decide`
(kernel reduction) or `decide +kernel` [ref-tactics] is preferred whenever
the instance reduces in acceptable time.

## 6. Tactics to try first

Core Lean, no Mathlib needed (all in the tactic reference [ref-tactics]
unless noted):

| goal shape | tactic | notes |
|---|---|---|
| closed proposition with a `Decidable` instance (quantifiers over `Fin n`, membership in a `List`, literals) | `decide` | rejects a target with local variables: quantify inside the statement or use `decide +revert`; `decide +kernel` reduces in the kernel once; `decide +native` is the trust downgrade of section 5 |
| `x = x` up to definitional unfolding, numerals | `rfl` | "for equality goals for types with decidable equality, usually rfl can be used in place of decide" |
| linear arithmetic over `Nat` and `Int`: `=`, `<`, `≤`, `k ∣ x`, `/ k` and `% k` with literal `k`, negations | `omega` | "not yet a full decision procedure" but "effective on many problems" |
| rewriting with `[simp]` lemmas and hypotheses | `simp`, `simp [h]`, `simp only [...]`, `simp_all` | `simp_all` iterates over hypotheses and target |
| casts between `Nat`, `Int`, and others | `norm_cast`, `push_cast` | `norm_cast` "is considered to be safe" where a non-terminal `simp` is not |
| fixed-width `BitVec` and `Bool` goals | `bv_decide` | external SAT solver with the proof verified inside Lean; native evaluation in the trusted base [validating] |
| goals an SMT-style engine closes: congruence, propagation, arithmetic | `grind` | separate chapter [ref-grind] |
| find the library lemma | `exact?`, `apply?` | search the environment; the answer they print is the proof to keep |
| structural steps | `intro`, `constructor`, `cases`, `induction`, `obtain`, `rcases`, `refine`, `exact`, `by_cases`, `split`, `contradiction`, `exfalso`, `assumption` | |

Mathlib, by the module that defines each tactic in the community tactic
list [mathlib-tactics]: `norm_num` (Mathlib.Tactic.NormNum.Core),
`linarith` and `nlinarith` (Mathlib.Tactic.Linarith.Frontend), `ring`
(Mathlib.Tactic.Ring.RingNF), `positivity`
(Mathlib.Tactic.Positivity.Core), `polyrith` (Mathlib.Tactic.Polyrith),
`field_simp` (Mathlib.Tactic.FieldSimp), `interval_cases`
(Mathlib.Tactic.IntervalCases), `fin_cases` (Mathlib.Tactic.FinCases),
`gcongr` (Mathlib.Tactic.GCongr.Core), `zify` and `qify`, `tauto`;
`aesop` comes from the Aesop package (Aesop.Frontend.Tactic), which
Mathlib pulls in. Real numbers, finsets, big operators, and the algebraic
hierarchy are Mathlib as well, so a statement over `ℝ` or with `Finset.sum`
needs section 3 before it elaborates.

Order of attempts on one elementary goal: `rfl`, `decide`, `omega`,
`simp`, `exact?`, `grind`; with Mathlib add `norm_num`, `linarith` or
`nlinarith`, `ring`, `positivity`. The strategy file fixes the number of
attempts per lemma.

## 7. The step directory

The harness runs the formal check in `attack/<slug>/deterministic/formal-check-<n>/`
(`../../harness/SPEC.md`). The directory is a Lake package:

```
formal-check-<n>/
  lean-toolchain        # a specific version, e.g. leanprover/lean4:v4.33.1
  lakefile.toml         # one [[lean_lib]] whose root is the source file
  lake-manifest.json    # written by the first lake build; committed
  .gitignore            # /.lake
  Main.lean             # or the file named in step.json; the sources
  step.json             # {"theorem": "<name>", "file": "<file>.lean"}
  README.md             # what the run decided, per the skill's stage 4
  result.json           # written by the harness, never by hand
```

`step.json` names the theorem the harness checks and the file that
declares it; `file` defaults to `Main.lean`. The harness runs `lake build`
in the directory, then the axiom check of section 4 on the named theorem,
and writes `result.json` recording pass or fail and the printed axiom list;
the decision rule is the one in section 4. A step with no `result.json`
has not run. The fixture `../../harness/fixtures/lean-smoke/` is the
smallest such directory and is what the harness's tests run against.

## 8. Encoding a lemma chain

One theorem per lemma of the blueprint, in dependency order, each
hypothesis an explicit argument, the final theorem proved by applying the
lemmas. A generic shape:

```lean
/-- Lemma 1: a linear bound the informal proof states for every `n`. -/
theorem lemma_one (n : Nat) (h : 2 ≤ n) : n + 2 ≤ 2 * n := by
  omega

/-- Lemma 2: the finite residue, decided in the kernel. -/
theorem lemma_two : ∀ k : Fin 8, (k.val * k.val) % 8 ∈ [0, 1, 4] := by
  decide

/-- The chain's final theorem depends on the lemmas, never the reverse. -/
theorem final_claim (n : Nat) (h : 2 ≤ n) :
    n + 2 ≤ 2 * n ∧ ∀ k : Fin 8, (k.val * k.val) % 8 ∈ [0, 1, 4] :=
  ⟨lemma_one n h, lemma_two⟩
```

Checked with the fixture's toolchain: the file elaborates, and
`#print axioms final_claim` reports `[propext, Quot.sound]` (from `omega`),
within the standard three.

Rules that keep the encoding honest:

- The statement is written before any proof, and read against the claim
  sentence in `problem.json` quantifier by quantifier: same domains, same
  constants, same direction of every inequality. A name proves nothing:
  "it is easy in Lean to define and name the statement that 2+2=5", and
  standard definitions can be overridden, so the reviewer checks that the
  formal statement is the claim, not only that it is proved
  [did-you-prove-it].
- Hypotheses of the informal proof, including a conditional hypothesis the
  attack has not discharged, are explicit arguments `(h : ...)`. A theorem
  with such an argument is a conditional result, and is labelled so.
- The skeleton is built first with every proof `sorry`; when `lake build`
  succeeds on it, the statements elaborate and the final theorem follows
  from the lemmas. Lemmas are then discharged one by one; `#print axioms`
  on the final theorem reports `sorryAx` until the last one is closed.
- Numerical constants are literals in the statement, so that the theorem
  checked is the theorem with its constants, as `formalise-while-fresh`
  step 4 asks.

When a statement is too far from the library to formalise within the
step's time: the objects have no definition in core or Mathlib (a construction
the attack itself introduced, a structure whose basic algebra would have
to be developed from scratch), or the proof needs a body of results whose
formal counterparts are absent, so that stating the lemmas requires new
definitions and each definition needs its own lemma library. Writing that
library is a project, not a step. The move then stops at the statement:
the skeleton with `sorry` is kept as the record of what was stated, the
missing definitions are listed, and the strategy's failure signal fires.

## Sources

Official Lean project pages:

- [lean-home] https://lean-lang.org/ (links to the install page and the
  reference manual)
- [install] https://lean-lang.org/install/ (recommended VS Code route;
  links to the manual steps)
- [install/manual] https://lean-lang.org/install/manual/ (the elan curl
  command, `source $HOME/.elan/env`, `lake new`, `lake build`, the Mathlib
  project command, `lake exe cache get`, updating Mathlib).
  https://docs.lean-lang.org/lean4/doc/quickstart.html and
  https://docs.lean-lang.org/lean4/doc/setup.html redirect to the install
  page.
- [elan] https://github.com/leanprover/elan (README: installer command,
  `~/.elan`, PATH, `lean-toolchain`, `elan show`, git prerequisite)
- [elan-init] https://elan.lean-lang.org/elan-init.sh (the installer
  script; flags `-y`, `--no-modify-path`, `--default-toolchain`)
- [ref-index] https://lean-lang.org/doc/reference/latest/ (served 4.34.0-rc2)
- [ref-axioms] https://lean-lang.org/doc/reference/latest/Axioms/
- [validating] https://lean-lang.org/doc/reference/latest/ValidatingProofs/
- [ref-tactics] https://lean-lang.org/doc/reference/latest/Tactic-Proofs/Tactic-Reference/
- [ref-grind] https://lean-lang.org/doc/reference/latest/The--grind--tactic/
- [ref-elab] https://lean-lang.org/doc/reference/latest/Elaboration-and-Compilation/
- [ref-lake] https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Lake/
- [ref-elan] https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Managing-Toolchains-with-Elan/
- [lake-readme] https://github.com/leanprover/lean4/blob/master/src/lake/README.md
- [lake-help-env] the output of `lake help env`, `lake help new`, and
  `lake help init` from Lake 5.0.0 on this machine

Lean community and Mathlib pages:

- [get-started] https://leanprover-community.github.io/get_started.html
  (points to the official install instructions)
- [project] https://leanprover-community.github.io/install/project.html
  (command-line project setup, `lake update`, `lake exe cache get`)
- [mathlib-readme] https://github.com/leanprover-community/mathlib4/blob/master/README.md
- [mathlib-wiki] https://github.com/leanprover-community/mathlib4/wiki/Using-mathlib4-as-a-dependency
- [mathlib-tactics] https://leanprover-community.github.io/mathlib4_docs/tactics.html
  (the "Defined in module" line of each tactic)
- [did-you-prove-it] https://leanprover-community.github.io/did_you_prove_it.html
