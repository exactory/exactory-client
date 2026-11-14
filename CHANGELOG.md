# Changelog

Each version has a full release note in [docs/releases](docs/releases/). The
newest version comes first.

Version [0.88.1](docs/releases/0.88.1.md) removes a store validation that 0.88.0 added before every command with a payload file. A command now reads the committed receipt of its request only when a path is refused with `unsafe_path`, so an artifact registration again makes two full validations of the history and one structural check. No reviewer route becomes stale.

Version [0.88.0](docs/releases/0.88.0.md) carries the first wave of fixes for the open harness-improvement records. Citations with TeX braces match their registry titles, and the problem exports carry a reference list. A post moves the obligations of its stopped review chains to `after_post`, and payload paths are recorded in their workspace-relative form. A safeguards notice of the provider is retried, usage windows are shown, search receipts give their gaps, packets are reused, and exports keep a history. A result series delivers each cited search record once, an openness chain reads the main capture that its packet bound, and an openness export assesses its minimum first. Every Claude route recorded before 0.88.0 needs a new probe.

Version [0.87.0](docs/releases/0.87.0.md) keeps a compact full text when the layout mode of PDF extraction pads it. A default PDF extraction whose layout text has a whitespace fraction above 0.90 or an expansion above 2.0 is extracted again without layout mode, and the complete plain text is kept, with both measurements in `extraction.layout_selection`. The new command `select-main-capture` records which available capture of an original the next deliveries use, without changing any capture or reading. No reviewer route becomes stale.

Version [0.86.0](docs/releases/0.86.0.md) makes two refusals of a review output name the facts that correct the output. A span on a delivered part that uses whole-text offsets is still `invalid_locator`, and the message now names the part's whole-text range and the part-relative offsets of the same span. `review_context_repair_mismatch` now names the expected events in recorded order, the missing events and the expected evidence. The texts that 0.85.1 to 0.85.3 added are rewritten, and the `--time-budget` section now states the usage error for input that the parser rejects. No reviewer route becomes stale.

Version [0.85.3](docs/releases/0.85.3.md) makes the `record-check --time-budget` bound the processor time of each reader instead of its elapsed time. A reader that waits while other processes use the machine passes, and a reader that uses more processor time than the budget still fails with `record_command_timeout`. The default budget is now 10,800 seconds of processor time, and the maximum is 100,000,000 seconds. Each reader row now gives `processor_seconds` next to `seconds`.

Version [0.85.2](docs/releases/0.85.2.md) makes a command report a local tool that did not finish within its own time limit, such as a `pdftoppm` page render, with the code `local_tool_timeout` instead of a traceback. `record-check` reports the failed step and `ready` false. An unfinished tool changes no review, records no refusal and is not cached.

Version [0.85.1](docs/releases/0.85.1.md) lets the Codex reviewer routes verify the input of Codex CLI 0.167.0. The three Codex transports accept its tool list only with the audited digest and its instructions message only when the text is exactly the request's instructions. Every Codex route recorded before 0.85.1 needs a new probe before its next review; recorded reviews keep their standing. `problem-candidate-transfer` now refuses a destination that does not exist or cannot be resolved, on every supported Python.

Version [0.85.0](docs/releases/0.85.0.md) lets a math-conjecturer record name the further areas that its statements concern, take up a candidate of another record with its forming history, and report how its exploration ended. `problem-area` records a further area, and each bar then states the standard of each area separately. `forming-import` gives a record a verified snapshot of the forming history of another record, and `problem-candidate-transfer` records the move in the source. `problem-report` records every form with its outcome, the candidates with their endings, each post with its classification, and one of six outcomes; `status` shows a due or stale report as an advisory.

Version [0.84.0](docs/releases/0.84.0.md) adds the accounts of a statement to the value evidence of math-conjecturer. Every new `problem-value` record keeps the conjectured answer apart from the question and enters each fact once: the evidence for or against the answer, the nearest alternatives with what the statement asserts beyond each, and the relations, with each consequence credited to the weakest alternative that gives it. A run is a test of the statement only when its plan declared the test before the execution. The result reviewers receive the accounts with a paragraph that says how the rubric reads them, and `problem-post-fact` records a fact that arrives after a post.

Version [0.83.0](docs/releases/0.83.0.md) lets math-conjecturer record how it forms a statement. An exploration can start from a phenomenon that appears in several places, and the Ideate tool gains two commands for a problem record: `forming` records each tentative statement with its alternatives, its expected relations and its planned checks, and `form-outcome` records how each ended. A value paragraph can cite an accepted result of any delivered math-solver objective, `problem-candidate` accepts the source kind `phenomenon`, and a slate that selects a candidate formed in the record cites a slate review.

Version [0.82.0](docs/releases/0.82.0.md) carries the bar and slate objections of a math-conjecturer record into another record that takes up one of its candidates: `problem-objections-carry` imports them with a proof of their source and local evidence, the slate and result reviews of the new record give a finding on each, and an unresolved one blocks the post. It ends the interim rule of 0.77.0, which kept every reviewed candidate in its record.

Version [0.81.1](docs/releases/0.81.1.md) corrects the automatic pass of 0.81.0 after its reviews: one automatic hop, from `experiment` to `write`, and one pass per hop; a ready gate needs no pass; an instruction recorded for an exact public write covers that write on every path; a failure to record a stop never changes the stop; real waits stay `waiting`; and the deposit description explains an automatic entry to a reader of the paper.

Version [0.81.0](docs/releases/0.81.0.md) gives every stop a class, `waiting`, `harness_defect` or `decision`, derived from its code, and lets the harness pass a stop that it records as its own defect. `review-series` records its final stop, and `exactory-research gate-auto-pass` takes only that stop's identity, checks the stop and the study, and moves the study from `experiment` to `write` without creating any review or decision. A second automatic pass of the same gate, and every public write after an automatic pass, need the user's recorded instruction.

Version [0.80.0](docs/releases/0.80.0.md) lets the math-solver controller record the user's instruction that sets one result aside or ends an objective. `search set-aside` takes one unaccepted result out of the mandatory acceptance step without accepting it, and `search end-objective` ends the objective without a closure. `search resume` and `search side-interval` accept the same reported form, so a paused objective comes back on the user's instruction. The record keeps the user's words, labelled as reported by the agent; the proof status and the obligations do not change, and the problem review sees the record. Without an instruction nothing passes.

Version [0.79.0](docs/releases/0.79.0.md) adds recorded overrides on the user's instruction for the stage moves of a study and for the export of a problem post. `exactory-research gate-override` pins the user's own words and records what the gate still owes; no gate reads the record, no review or decision is created in place of a skipped step, and a deposit names the recorded gates in its public description. The math-solver controller is not included, and without an instruction nothing passes a gate.

Version [0.78.0](docs/releases/0.78.0.md) keeps a session working when the file guard cannot check a tool call. Six read-only tools and eight plain shell commands pass, and every other call is denied with a reason that names the error, tells the agent to report it, and names the controls that only the user has. A definite write to a file that the harness owns is denied as before.

Version [0.77.0](docs/releases/0.77.0.md) keeps every candidate of a math-conjecturer exploration in its record once the record's first review is requested. A review objection binds the candidates of its own record only, so a reviewed candidate no longer moves to another record without its objections. The full fix, which carries the objections across records, is planned.

Version [0.76.0](docs/releases/0.76.0.md) makes the files that the harness owns read-only on disk. The native commands and hooks publish each such file atomically and without write bits. A direct write by an agent, such as a shell redirection into the journal of an attack, then fails with a permission error. The files that agents write by design stay writable, and records from earlier releases keep working.

Version [0.75.0](docs/releases/0.75.0.md) makes `status` faster on review-heavy records, closes review work per posted statement instead of per record, and adds managed Codex hooks that survive a plugin update. It also corrects source delivery, round evidence, citation locators, native review packets, the continuation of `make-the-proof-constructive`, the restoration of withheld review bodies, and the check of the actual Codex model and effort.

Version [0.74.1](docs/releases/0.74.1.md) adds an entry to the `math-conjecturer` skill for open problems that start from a simple thought experiment, as the Kakeya needle problem did. `problem-candidate` accepts the source kind `thought_experiment`. Such a problem meets the same posting bar as every other problem, and its exploration keeps all reviewed work in one record.

Version [0.74.0](docs/releases/0.74.0.md) lets `record-check` rebuild the failed historical requests of three records that 0.73 refused, adds daily maintenance of the CI Lean cache, corrects false refusals and repeated Stop notices of the math-search hook, stops rebuilding an oversized bundle index, and bounds measurement campaigns at the first usage limit. The `math-conjecturer` skill records a problem that was posted outside the post gate.

Version [0.73.6](docs/releases/0.73.6.md) makes a recorded post terminal for review work on the posted statement. New bar, result and adjudicator reviews, reviewer calls, staged planning and output corrections of that statement are refused with `problem_already_posted`, and the views show no further review action. Replays, captured outputs, reconciliation and the readers of stored records are unchanged.

Version [0.73.5](docs/releases/0.73.5.md) stops failed Lean downloads from failing CI. Only the jobs that run Lean install it, from the GitHub Actions cache or in up to four timed attempts, and a skipped harness test now fails CI. The harness code does not change.

Version [0.73.4](docs/releases/0.73.4.md) makes CI of 0.73.3 pass. A replay test of a 0.69.18 review history now expects the pending occupancy that 0.73.3 reports. The harness code does not change.

Version [0.73.3](docs/releases/0.73.3.md) records the time of a problem post that was made outside the harness, and adds `problem-post-time` for such a post that an earlier version recorded. The post gate keeps a valid adjudication while the result reviews are refreshed, and it reports a refreshed result slot as pending work.

Version [0.73.2](docs/releases/0.73.2.md) raises the default time budget of `record-check` from 900 to 3600 seconds, so that a record whose readers work but take longer than 15 minutes passes the report step.

Version [0.73.1](docs/releases/0.73.1.md) makes the integrity step of `record-check` read only the artifacts of the store. It counts the other file descriptors of the history, such as the publication scope's projections of workspace files, as `workspace_descriptors` instead of refusing the record.

Version [0.73.0](docs/releases/0.73.0.md) adds `review-series`, which runs a review chain to its end, and `record-check`, which checks a copy of a record offline before the record moves to a new version. A failed reviewer call now has an outcome that decides the next step. An answer with exactly one reading is kept, a stuck parts v1 series continues in parts v2, and a posted problem stays posted. Every recorded incident is in a regression ledger with its test.

Version [0.72.4](docs/releases/0.72.4.md) makes the request memo of 0.72.2 call the pinned request builder with the arguments of the pinned iterator, so test replacements without an `order` parameter work again. Requests, reports and refusals do not change.

Version [0.72.3](docs/releases/0.72.3.md) keeps the request memo of 0.72.2 to plain list histories and tuple fit orders; other argument types take the uncached path. The harness's own requests, reports and refusals do not change.

Version [0.72.2](docs/releases/0.72.2.md) makes a command derive each review request of an assignment once and share it between request selection and attempt authentication. On a problem record with 141 review assignments, one `status` called the request builder 1,214 times on 102 distinct inputs. The requests, packets, reports and refusals do not change, and no route-pinned module changed.

Version [0.72.1](docs/releases/0.72.1.md) adds the protocol `exactory-openness-reading-v5` for new problem openness reading chains. Search fields that several bound searches share arrive once, and the harness rebuilds every original search from the delivered parts before it gives credit. A v5 final turn can withhold a selected source text that earlier turns delivered completely, and each `review-assignment` reports the final-input headroom. Chains that earlier releases started continue under their own protocol.

Version [0.72.0](docs/releases/0.72.0.md) delivers a review whose input does not fit one route input as a series of fresh-input turns (`exactory-review-parts-v2`): read, combine, check and final, with an append-only inventory, verified statements that keep their locations and a state budget that makes the final fit. The problem result, the focused adjudication, the context repairs, the study verification and readiness reviews and the native mathematics review use it. Six focused adjudications that 0.71.x refused with `review_input_too_large` plan as such series on record copies. The quality of a parts series against a single-input review is not yet measured. It also bounds the retained reviewer excerpt state (`exactory-review-excerpts-v3`).

Version [0.71.8](docs/releases/0.71.8.md) makes the file guard's controller, workspace and output-folder discovery treat `ENAMETOOLONG` as absence, so an overlong parent component or whole path no longer stops an unrelated tool call. Ancestor and remaining-target checks continue, and other unexpected check failures still deny the call.

Version [0.71.7](docs/releases/0.71.7.md) gives newly rendered reviewer prompts common text version 3. It tells the reviewer to write each top-level field inside the one JSON object and to close that object once. Its aim is fewer reviewer calls lost to an answer that is not one object. Recorded prompts keep their text and their verification.

Version [0.71.6](docs/releases/0.71.6.md) settles reviewer source requests with revalidated registry-absence or terminal-origin evidence, without granting reading credit or waiving critical source obligations.

Version [0.71.5](docs/releases/0.71.5.md) makes the file guard's workspace, output-folder and controller discovery read the file status itself, so on Python 3.14, where pathlib's `exists()` and `is_file()` return False on every `OSError`, a check that fails still denies the call instead of allowing it.

Version [0.71.4](docs/releases/0.71.4.md) lets the file guard skip a shell word or tool path only when its check raises `ENAMETOOLONG`, so an overlong heredoc word or file path no longer stops an unrelated tool call. Other unexpected check failures still deny the call.

Version [0.71.3](docs/releases/0.71.3.md) reads a rollback journal that a committing writer has just unlinked as absent, so a store reader no longer reports `store_busy` while another process commits.

Version [0.71.2](docs/releases/0.71.2.md) lets a full-text job finish for a work that is not an arXiv version, such as a DOI work whose PDF comes from its publisher, and corrects seven texts of 0.71.0 that agents read.

Version [0.71.1](docs/releases/0.71.1.md) lets a store reader retry when a committing writer deletes the rollback journal while the reader checks it, instead of reporting an unsafe path.

Version [0.71.0](docs/releases/0.71.0.md) carries the records left open after 0.70.0: an oversized problem result returns to the part delivery of 0.69.x unless a staged review is requested, every ordinary review is judged on what its exact request delivered, a request is checked before any reviewer call exists and every ordinary call is captured, enumerations and batches state their bounds, exports and deposits stream, large originals get a resumable full-text job, provider pacing is shared across local processes, and the math-solver audits, replay, render and hooks stop repeating work.

Version [0.70.2](docs/releases/0.70.2.md) lets a problem result final count objection-evidence bodies that its own series delivered, and delivers undelivered ones in a part turn of their own when the final would not fit.

Version [0.70.1](docs/releases/0.70.1.md) also replaces, in the replayed history of a refreshed problem result slot, the part outputs of an earlier series that ended when the problem evidence changed, when the turn would not fit otherwise.

Version [0.70.0](docs/releases/0.70.0.md) performs a problem result review or adjudication that does not fit one input as staged tasks with fresh inputs, and records an assessment that cannot be completed as an occupied slot.

Version [0.69.20](docs/releases/0.69.20.md) lets a refreshed problem result slot continue when its replayed history no longer fits: after the part planner refuses, the outputs of closed earlier series become short placeholders and the turn is planned again.

Version [0.69.19](docs/releases/0.69.19.md) lets a problem result review in a restarted part series cite records that earlier part series of the same review slot actually delivered.

Version [0.69.18](docs/releases/0.69.18.md) registers the record kind and the command `problem-citation-metadata` in the tables that the registry tests read.

Version [0.69.17](docs/releases/0.69.17.md) makes `minimum.text_priority` of an openness export name the full texts that the packet carries, and refuses a listed version without one.

Version [0.69.16](docs/releases/0.69.16.md) treats a BibTeX directive head at the start of a line as a boundary (so the text of a braced `@comment`, `@string` or `@preamble` that starts a line is not evidence) and normalizes titles in linear time without cutting combining marks; 0.71.0 lists the directive forms that it does not cover.

Version [0.69.15](docs/releases/0.69.15.md) adds `problem-citation-metadata`, which gives a `url:` work of a problem record its citation title and authors from exact byte spans of retained captures.

Version [0.69.14](docs/releases/0.69.14.md) ends an identifier at the closer of its bracket or quote only outside its braces, skips entry heads inside braced entries, limits Unicode normalization, and reads quoted plain-text URLs again; 0.69.16 extends the directive heads and replaces the normalization limit.

Version [0.69.13](docs/releases/0.69.13.md) reads each BibTeX entry by its top-level fields for titles and identifiers, and reads TeX escapes before percent-decoding; 0.69.14 corrects quotes inside braces and nested entry heads.

Version [0.69.12](docs/releases/0.69.12.md) ends a URL or a DOI in a bibliography only at whitespace, at the closer of a bracket or quote that opened it, or at an unmatched closing brace, and reads BibTeX escapes in DOIs; 0.69.13 applies the rule per BibTeX field.

Version [0.69.11](docs/releases/0.69.11.md) ends URLs and DOIs in a bibliography by their context (angle brackets and Markdown links only where they open the identifier, balanced closing brackets), accepts URL schemes of any length, and reads unclosed entries in linear time; 0.69.12 replaces the ending rule.

Version [0.69.10](docs/releases/0.69.10.md) reads each bibliography entry once into whole identifiers (arXiv ids and DOIs) and counts a DOI only as a whole DOI or through doi.org; later releases correct how identifiers end.

Version [0.69.9](docs/releases/0.69.9.md) finds URLs and DOIs in a bibliography in one pass and credits `arXiv:` labels, `eprint` fields and old-style ids only outside them; later releases extend the rule.

Version [0.69.8](docs/releases/0.69.8.md) reads arXiv ids in a bibliography by kind (arxiv.org URLs by host and path, the DataCite DOI, `arXiv:` labels, `eprint` fields and old-style ids) and ignores BibTeX keys; later releases finish the reading of nested URLs and DOIs.

Version [0.69.7](docs/releases/0.69.7.md) matches an arXiv id in a bibliography only after an arXiv context (`arXiv:`, an arxiv.org URL, the DataCite DOI or an `eprint` field; read by kind since 0.69.8), and keeps a work whose identifiers name two arXiv papers out of problem citation links.

Version [0.69.6](docs/releases/0.69.6.md) narrows where an arXiv id can match in the manuscript citation check (continued in later releases), and links problem citations only through a work's own id and its DOI aliases.

Version [0.69.5](docs/releases/0.69.5.md) cites a paper once in a problem export when a record read it under both its arXiv id and its DataCite DOI, and corrects one paragraph of the 0.69.4 release note.

Version [0.69.4](docs/releases/0.69.4.md) resolves frozen record citations without a digest when delivered copies identify one known version or have equal projections.

Version [0.69.3](docs/releases/0.69.3.md) requires objection bodies in fitted final requests and lets continued units skip bodies that do not fit.

Version [0.69.2](docs/releases/0.69.2.md) delivers a problem evidence unit's artifact bodies across part turns and reduces objection evidence in part packets.

Version [0.69.1](docs/releases/0.69.1.md) makes the planning of a review turn fast on a large store.

Version [0.69.0](docs/releases/0.69.0.md) delivers oversized problem result evidence in validated parts of the same reviewer session.

Version [0.68.0](docs/releases/0.68.0.md) separates the outputs of math-solver, ai-science and math-conjecturer into
two folders that the harness generates in the workspace root. `final/` holds the current result of each step, with
the superseded results in `final/history/`, and `intermediate/` holds every other output, item by item, with its
status. Each reviewer series gets one folder with a `series.json` that lists every turn and run. The problem export
and the review packets go into these folders by default, and the guard hook refuses direct edits there.

Version [0.67.1](docs/releases/0.67.1.md) checks the declaration of an openness minimum at export with the audit's
own rules, so a packet that no resolved finding can pass is refused before any review runs. `exactory-lab run` and
`reconcile-run` print the execution id and the output descriptors that a result reference of `assess` needs, and a
problem workspace's `status` reports the literature digest that `cycle` compares. The agent-facing texts of 0.67.0
and 0.62.0 are corrected where an independent review found them false or unclear.

Version [0.67.0](docs/releases/0.67.0.md) ships the work of the harness-improvement records that were open before
it, except storing each evidence value once, which the owner decides on separately (the release note's known
limits): a review observation survives a busy store, a disabled Codex `exec` call that ran nothing is a notice,
every recorded result output reaches its reviewer, a refused review output names the failed rule and item, new
reviewer prompts carry a versioned common text that forbids tool calls and states the `RETRACTED` form, every
writing command accepts `--check` and writes nothing under it (many refuse it with `check_unsupported`), the
draft-marker obligation names a step that works in every state, `exactory-lab run` works in a problem record, and
a math-solver result review with an oversized openness minimum reaches its reviewer as a reading chain. The
release also places the agent-facing texts of the math-solver repairs of 0.64.0, of check mode and the Codex
reviewer routes of 0.66.0, of format-only second runs, the five lanes of 0.65.0 and the problem-record launcher,
of the draft marker and the lanes of this release, and of the openness reading chain, and it corrects five
statements that shipped false.

Version [0.66.3](docs/releases/0.66.3.md) bounds the final turn of an openness reading chain: the packet's records
arrive as reading units after the full texts, the final turn withholds delivered bodies and then the oldest notes
only as far as its request needs, and a refusal names the size of each part.

Version [0.66.2](docs/releases/0.66.2.md) lowers the peak memory of history validation: the UTF-8 check and the
digest of a stored value run in pieces of 65,536 characters, and each completed event is released before the next
one is read. A push starts CI only on `main`; pull requests start CI as before.

Version [0.66.0](docs/releases/0.66.0.md) stops code execution in the Codex reviewer routes, names a tool call
in the attempt evidence, refuses an oversized Codex input before dispatch, and adds `--check` to every mutating
`exactory-research` command, which reports the first refusal without writing anything. History validation no
longer repeats the JSON type check for stored values, which makes a write on a large store faster.

Version [0.65.2](docs/releases/0.65.2.md) bounds the reading of one review turn: a turn delivers at most 120,000
characters of new full text, a longer text arrives in parts over consecutive turns, and a turn whose run ended
without output can be replaced by a new continuation of the same parent.

Version [0.65.1](docs/releases/0.65.1.md) repairs a test of 0.65.0 that still called two renamed test helpers by
their old names. The harness code is unchanged.

Version [0.65.0](docs/releases/0.65.0.md) accepts run outputs of any media type as result evidence, seals
completion-check outputs that the author did not read and discloses program runs before a plan, stores new
manuscript review inputs by reference, packages the code, inputs and outputs of cited runs for a deposit, reports
DOI family conflicts at import, and makes a small write on a large study store about a third faster and lighter.

Version [0.64.2](docs/releases/0.64.2.md) lets a review read more full text than one route input can hold.
The reviewer keeps verbatim excerpts that the harness checks against the stored source text, a repeated request
for a delivered text is answered as already delivered, and a problem openness review whose texts do not fit one
input reads them in a chain of turns.

Version [0.64.1](docs/releases/0.64.1.md) repairs nine defects that a review of the 0.62.0 code found: the
manuscript drift report no longer reads pinned objects or fails `status`, `restore` keeps every edit and refuses
path aliases, JATS extraction refuses unresolved entities and has resource bounds, and format-only reruns leave
closed cycles closed. It also renames the functions of 0.61.0 and 0.62.0 that had no verb.

Version [0.64.0](docs/releases/0.64.0.md) repairs Claude review delivery and preserves standalone proposal
reviews after an unrelated node finishes. Allocated side work runs before root finalization, and closure waits
for admitted standalone nodes to finish or retreat. Admission fills missing attack files. Activity logs track
harness sessions, and hooks name the runtime that wrote an unreadable record. The release note records the
upgrade steps, provisional choices, remaining text gaps and work for later releases.

Version [0.63.0](docs/releases/0.63.0.md) lets `exactory post-proof` post a partial result to a problem in the
Mathematics section: a proof of its own stated result that bears on the problem. A partial result never resolves the
problem, and every claim now names its method. The math skills say when to post a partial result and how to read
those of other accounts. math-solver prepares the files and commands of a post, and the user's instruction sends it.

Version [0.62.0](docs/releases/0.62.0.md) reads JATS XML articles and keeps the images of a PubMed Central figure
together. It checks claim evidence before the manuscript pin, reports changed bundle files and restores their pinned
bytes with backups. It creates a draft marker from explicit study metadata, admits mechanically checked format-only
reruns, and gives missing hook scripts a message and an exit status for their event. The release note records the
provisional choices, the remaining gaps and the work left for later releases.

Version [0.61.0](docs/releases/0.61.0.md) reads PDFs served as `binary/octet-stream` and the article body of a
PubMed Central page. Review delivery and its projection agree on media-type spellings while earlier reviews still
authenticate. A session started for one assignment can stop inside an autopilot study without changing its counter.
The full-text reference states how redirects and retries count against the request budget.

Version [0.60.1](docs/releases/0.60.1.md) repairs the threshold checks of a posted statement's self-assessment and
result reviews graded before the admission item A7, direction after a refutation. Their below-threshold shortfalls
are listed again, and a current focused adjudication can release the shortfall of a review. The behavior for a
statement not yet posted is unchanged.

Version [0.60.0](docs/releases/0.60.0.md) makes problem selection after a refutation an absolute principle: choose
questions about the world the refutation reveals. It adds the admission item A7, direction after a refutation,
and version 5 review templates. Earlier assessments and result reviews that lack A7 must be graded again before
posting a statement that is not yet posted; earlier records and posted problems stay unchanged.

Version [0.59.0](docs/releases/0.59.0.md) lets result coverage rely on the bound search records when a text route
withholds their captured response bodies. It also corrects Darwin process inspection, installs the PDF renderer
from the Ubuntu archive only in continuous integration, and fixes reassessment of compacted scientific history.

Version [0.58.1](docs/releases/0.58.1.md) repairs the coverage check of a problem result review. 0.58.0 refused the
whole actual request of a result in a bar slot that a context repair continued after the author recorded the statement.

Version [0.58.0](docs/releases/0.58.0.md) ships the repairs that two problem records made in local runtimes
on 2026-10-21 and 2026-10-22: a fixed order of input reductions on local routes, whole-source batches, two new Codex
routes for framed and visual evidence, the version 4 result prompt, distinct identities for colliding objections,
the `not_binding` adjudication, the native strategy obstruction milestone and standalone results, and repairs of the
math hook and `exactory-lab probe`. It reads the records that those runtimes wrote.

Version [0.57.0](docs/releases/0.57.0.md) sends a problem statement in two parts. The text above the one line
that holds only `---` is the statement, which the problem page shows in a box. The text below that line is the
details, which the page shows under the box.

Version [0.56.1](docs/releases/0.56.1.md) repairs the post gate of a problem
record whose result reviews cite delivered records by kind and id, applies the
review binding to objections and reassessment findings, and lets the Claude
Code route verify a fenced JSON answer and the thinking-token accounting event
of effort `max`.

Version [0.56.0](docs/releases/0.56.0.md) gives a Claude Code session its own
reviewer route, so that the independent reviews of a record run inside the
session that opened it. It also repairs the defects that the first problem
record met on 2026-10-20 and 2026-10-21.

Version [0.55.1](docs/releases/0.55.1.md) corrects the delivery boundary of
the problem reviews: a delivered passage carries a citation only when the
receiving role can record it, the adjudicator's output is checked against the
content the adjudicator received, and a review output that fails the output
contract leaves a refusal record and permits one retry of the same assignment
instead of blocking its slot.

Version [0.55.0](docs/releases/0.55.0.md) adds the research half of
`math-conjecturer`: the harness creates an open mathematical problem as
research before it posts it. A problem record holds an exploration with its
candidates and the slate that selected some of them, an attempt on the exact
statement with math-solver or from full readings, and a rubric self-assessment
graded by two independent result reviews, in addition to the statement, the
openness finding, the value evidence and the post decision of 0.53.0. The
operations of the Mathematics section move to the new skill `math-section`,
and `search deliver-attempt` delivers a math-solver attempt, resolved or not,
into the problem record.

Version [0.54.0](docs/releases/0.54.0.md) follows the Mathematics section of
exactory.ai, where the ORCID iD became optional. Any signed-in account writes,
and a write from an account with a linked ORCID iD carries that iD. `exactory
problems` and `exactory discussion` gain filters to ORCID holders, and each
problem carries its ORCID upvote count.

Version [0.53.0](docs/releases/0.53.0.md) adds problem creation as an
orchestration of the research harness. A problem record holds the claim that
a problem is precisely stated, open and worth solving, with its evidence and
its reviews, and exports the parts that `math-conjecturer` posts. The
openness minimum of math-solver is shared with the record, and `search
deliver-closure` delivers a native completion to it. A workspace path with a
parent segment now resolves to the named directory.

Version [0.52.1](docs/releases/0.52.1.md) corrects two errors in the handling of
a process that ends during a lookup or a signal: the Linux process lookup of the
probe supervision reports it as absent, and the native termination of math-solver
keeps the record of the command.

Version [0.52.0](docs/releases/0.52.0.md) makes the research actions tools
that a study uses at any stage, with amounts that each call states, and keeps
the gates on the claims. A depth plan sets the default amounts, a probe runs an
exploratory calculation before a direction exists, and every search records
its coverage. The literature stage ends at a direction gate with a fixed
minimum of evidence and a Q1 bar. math-solver closes only with a supported
openness finding. Claims stay current after unrelated readings, searches and
synthesis changes, and the harness authenticates recorded inputs instead of
recomputing them from current values.

Version [0.51.0](docs/releases/0.51.0.md) connects the plugin to the
Mathematics section of exactory.ai. Accounts with a linked ORCID iD post open
problems, Lean formal statements as GitHub links, proofs, and reports of Lean
checks run elsewhere, and every signed-in account votes and reports. In 0.51.0
the `math-conjecturer` skill ran these operations; the `math-section` skill runs
them now. A deposit can name a problem and attach its published record to it,
every direct deposit recovers by reading its deposition back, and a deposit
records a Zenodo answer only for the deposition that its request named.

Version [0.50.1](docs/releases/0.50.1.md) adds the literature repairs and the
store bounds that 0.49.0 left out. A search judgment owes full readings of the
works it cites only while it is the selected judgment of its purpose, a
citing-works capture binds its OpenAlex filter, and the citation graph reads one
bundle for each original. The store writes only JSON that every supported Python
reads back, and a command input nests at most 32 levels. `exactory-derive`
reports malformed and complex inputs without a Python traceback.

Version [0.50.0](docs/releases/0.50.0.md) adds scientific contribution decisions
before committing an objective and after results. Managed studies compare
consequences and approaches, retain critical reviewer work, and bind independent
assessments to observed reviewer contexts. One phase-specific decision serves
existing target, cycle, writing, publication and round commands. This is an
experimental workflow change; scientific efficacy is not established.

Version [0.49.0](docs/releases/0.49.0.md) waits for a slow local worker or
math-solver launcher as long as for its run. In the cases it repairs, a launch
whose worker or launcher ends early or cannot start records the run's outcome
or ends with an error that names the recovery, instead of a Python traceback.
The experiment guard denies a write by its target, so reads of workspace state
pass, and `assess` refuses a passed validity check without validation
evidence.

Version [0.48.0](docs/releases/0.48.0.md) starts a program admitted with a
venv's `bin/python3` through that link, so it keeps the venv's packages. It
refuses, before a run, a figure or other binary output bound to `result` or
`validation` evidence, and it can take a run's metric from the declared JSON
validation output.

Version [0.47.0](docs/releases/0.47.0.md) lets the user change the fixed
objective of a research study. `target` accepts the change only with a pinned
`context/` file that holds the user's instruction; the earlier objective stays
retained as the predecessor, and reviewers see the change.

Version [0.46.0](docs/releases/0.46.0.md) captures full-text originals up to
128 MiB (was 32 MiB), so arXiv PDFs with large figures can be read in full, and
keeps the publication date of every record in a mapped import.

Version [0.45.0](docs/releases/0.45.0.md) reports the server's new rule for
paper versions: when the account that opened a paper's open verification
submits a later version, the verification moves to that version, and the
earlier one closes as superseded. `exactory submit` names the replaced
verification, and a task or verdict on a superseded verification names its
successor.

Version [0.44.0](docs/releases/0.44.0.md) lets a study continue without a source
that it cannot obtain, through an audited `defer-source` decision, and publish
the supported part of an open objective under an explicit source-limited
contract. `fulltext` reads scanned PDFs with bounded OCR and acquires a
supplement through an assessed correspondence with its main article. A scoped
manuscript reaches blind reviewers through a checked scientific delivery. The
Zenodo disclosure states that the human author is responsible for the content.

Version [0.43.0](docs/releases/0.43.0.md) makes a manuscript account for the
literature that its study used. The manuscript pin covers every work that the
study read in full and every work that a selected five-purpose search cites.
Each of these works is cited, or has a stated reason. `exactory-check add
--arxiv-id` cites the published version when one exists. `lookup` blocks
bibliography entries that the manuscript never cites.

Version 0.42.2 completes [0.42.0](docs/releases/0.42.0.md), which directs every
research study at the challenges ahead of it. A study records those challenges
before the objective: the ultimate goal of its field and nearer large goals, each
with criteria. This Grand Challenge record belongs to the study and is separate
from a Grand Challenge posted on exactory. Every blind review names what each
score below 4 needs to reach 4. After each measurement the author investigates
briefly and records a contribution analysis; the next manuscript bundle waits for
it, and the round decision reads it. [0.42.1](docs/releases/0.42.1.md) keeps the
investigation as captured responses, so the measured bundle stays current, and
[0.42.2](docs/releases/0.42.2.md) names the record before the objective in
`next` and lets only an approved round decision bind a later goal.

Version [0.41.0](docs/releases/0.41.0.md) prepares a study from bounded reading:
`lineage-v1`, the research default, follows the line of results the paper
continues through a bounded loop over the five search purposes, and `sampled-v1`,
the verification default, reads one stratified random sample of the frozen
population. `submit` and `verify` run from any directory; `deposit` runs in a
draft workspace, from its root. A ready study records its receipt. A workspace
that refuses the receipt gets one stderr line. A refused receipt stops no request.

Version [0.40.0](docs/releases/0.40.0.md) runs `exactory submit`, `exactory
verify`, and `exactory-draft deposit` on the user's instruction. Version
[0.39.2](docs/releases/0.39.2.md) enforces the final round decision at deposit,
separates internal claim history
from blind review, and measures the three paired reviewers. Version
[0.39.0](docs/releases/0.39.0.md) develops a paper across research rounds. At the
end of a round, the study decides on the exact manuscript bundle to continue with
a new goal or to stop. An independent assessor must approve this decision. Each
new goal has success criteria and stop conditions. In each later round, the study
runs four searches on the consequences of the paper. It also reads in full one
paper that developed a comparable first result further. The manuscript keeps each
earlier claim, or marks the claim as revised or superseded. Each blind measurement
review also predicts the cohort percentile of the paper. An optional `development`
budget limits the number of rounds. Version [0.39.1](docs/releases/0.39.1.md)
corrects the documentation of these rounds and adds tests. These versions retain the proportionate
literature preparation of [0.38.0](docs/releases/0.38.0.md), the shared research
foundation of [0.36.0](docs/releases/0.36.0.md) and
[0.37.0](docs/releases/0.37.0.md) and the mathematical strategy reassessment of
[0.35.0](docs/releases/0.35.0.md). Version [0.34.0](docs/releases/0.34.0.md)
adds the math search controller that Claude Code and Codex share. Versions
[0.34.1](docs/releases/0.34.1.md) and [0.34.2](docs/releases/0.34.2.md) repair its
ranking, task recovery and journal. Version [0.33.1](docs/releases/0.33.1.md) holds
the Claude Code and Codex entrypoints, the 14 shared skills and their hooks.
