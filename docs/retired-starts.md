# Retired starts of a problem exploration

From 0.83.0 to 0.91.2 a problem record stated a kind of start in a `start` entry. The
[math-conjecturer skill](../skills/math-conjecturer/SKILL.md) gave a procedure of its own for
two kinds, each with a candidate source kind and a forming question kind of the same name:

- `phenomenon`, a phenomenon that appears in several places. The skill offered it from 0.83.0 to
  0.91.2.
- `thought_experiment`, a simple thought experiment. The skill offered it from 0.74.1 to 0.91.2,
  and before 0.83.0 a record stated no kind of start.

Release 0.91.3 removed the kind of start. A new record holds none, and the skill has no step
that chooses one. This document is for a problem record that an earlier release opened with one
of the two starts, and for a record that holds a candidate or a forming call of one of the two
kinds. Such a record keeps working, and this document holds what the skill stated for it up to
0.91.2. A name in quotation marks is a section of the skill. The skill states everything that
this document does not, and the [CLI reference](research-cli.md#the-removed-kind-of-start)
gives the refusals.

## What the harness accepts

- A record holds a retired kind when its `start` entry names the kind, when one of its
  candidates has it as the source kind, or when one of its forming calls has it as the kind of
  its question. The rule is applied to `phenomenon` and to `thought_experiment` apart.
- In a record that holds the kind, `problem-candidate` accepts it as a source kind and
  `forming` accepts it as the kind of a question. A new version of a candidate, which names the
  earlier one as `previous`, is one case. Every other record refuses the kind
  (`problem_start_kind_removed`).
- `problem-init` refuses a `start` entry (`problem_start_kind_removed`), so no new record holds
  a kind of start.
- A record that was opened from a thought experiment before 0.83.0 has no `start` entry. It
  holds the kind through a candidate or a forming call of the kind. Where it holds neither,
  record its candidates and its forming calls as "A new record that takes up a candidate of
  such a record" states for a new record.
- Every other rule of the harness is as it was in 0.91.2, and the sections below state the
  rules that concern these records.
- The stored records keep their bytes and their digests, and `status` shows the start under
  `problem.start` as before.

## Continue a record of a retired start

1. Before you use a new harness version on the record, run `exactory-research record-check`, as
   the skill states for every existing record.
2. Read the section of the record's start below: "An exploration from a thought experiment" or
   "An exploration from a phenomenon that appears in several places". Its first step is done.
   Go on from the step that the record has reached.
3. Apply the rules of the next section where the shared sections of the skill no longer state
   them.
4. Follow the skill for every other step: the slate, the formulation, the attempt, the openness
   finding, the value record, the reviews, the decision, the post and the report.

## Rules of the shared sections for these records

Up to 0.91.2 the shared sections of the skill stated the rules below. They hold for a record of
a retired start as before.

- The source kind `thought_experiment` ("Exploration", the kinds of source): a question derived
  from a thought experiment of this exploration ("An exploration from a thought experiment",
  below). The reference names the seed, the interpretation and the expectation that the
  candidate derives from, with the expectation's outcome, because the slate reviewer reads the
  reference and the observation and not your notes; the evidence cites the records that give
  that outcome, in the evidence shapes of "Value and the rubric", step 6.
- The source kind `phenomenon` ("Exploration", the kinds of source): a statement that this
  record formed from a phenomenon that appears in several places ("An exploration from a
  phenomenon that appears in several places", below). The reference names the phenomenon, the
  move, the forms that the candidate derives from and the outcome of each, for the same reason.
  The evidence cites the forming calls and the outcomes as records, and at least one forming
  call (`problem_candidate_forming_missing` otherwise).
- An imported forming call counts ("The forming history of a candidate taken up in another
  record", step 5): a `phenomenon` candidate needs at least one forming call, its own or an
  imported one.
- A sibling record ("Exploration"): a sibling record on the same seed needs the carry in both
  directions, as a sibling record on the same field question does.
- The comparison of the slate ("Candidates and the slate", step 6): an exploration opened on a
  phenomenon that appears in several places gives priority to the candidates for which it
  expects to establish a gain ("An exploration from a phenomenon that appears in several
  places", below). Such a candidate states in its `observation` the form of the gain, what the
  statement would gather, how the statement would stand to the conjunction of what it gathers
  (equivalent, stronger in a stated respect, or not known), and the work that would establish
  the gain. The number of statements that would follow from a candidate gives no priority. This
  priority is a reasoned departure from the preference for accessible advances (class `E-F`
  first, in "The rubric"); record it once for the exploration, in the comparison.
- The slate review ("Candidates and the slate", step 7): a candidate of the kind `phenomenon`
  is a candidate formed in the record, so a slate that selects it cites a slate review
  (`problem_slate_review_required` otherwise).
- The report ("The end of an exploration"): the report is due for a record whose start is
  `phenomenon` or `thought_experiment`, once the exploration has ended.

## A new record that takes up a candidate of such a record

The skill sends a candidate to a new record in three cases: a candidate of another subject
class, a sibling record, and a further area that is needed after a slate review or a result
review was assigned ("Exploration"). A record opened since 0.91.3 holds no kind of start and no
retired kind, also when it continues an exploration of a retired start. It needs neither: the
forming import keeps the forming calls of the source with their kinds, and every rule that the
two kinds carried is reached through the forming records that the candidate cites.

1. Open the new record as "Exploration" states, without a `start` entry. Describe in `field`
   the question that the candidate asks, and name the record that it continues in your notes.
2. Bring the objections with the carry and the forming history with `forming-import`, and
   record the transfer in the source ("Carry a reviewed candidate to another record", "The
   forming history of a candidate taken up in another record").
3. Record the candidate with the source kind that names its material: `literature` for works
   read, `computation` for a probe pattern, `own_record` for your solver attacks, `user` for
   the user's material. Cite the imported forming calls and form outcomes in
   `source.evidence`. The candidate is then a candidate formed in the record: a slate that
   selects it cites a slate review, and the cited forming records are the history that a gain
   claim and a passed test need ("The accounts").
4. Write its `source.reference` and its `observation` as the rule of its former source kind
   states above, because the slate reviewer reads both.
5. Apply in the new record the remaining steps of the section of the start below, with its
   principle and its openness requirements. State the question of a forming call of the new
   record under a kind in your own words, and list in `places` the places that it concerns.
6. The report of the new record is due once it holds a forming call of its own, and you can
   record one in every case ("The end of an exploration").

## An exploration from a thought experiment

A thought experiment is a simple situation of the physical world, or an intuitive question about
one, stated in plain words before any mathematics is chosen; turning a unit segment around in the
plane, the start of the Kakeya needle problem, is the model. An exploration that starts from one
is an exploration as "Exploration" describes it, with its budget, its tools, its slate and its
end, and a candidate derived from it meets the same thresholds as every candidate. This section
gives the research that turns the situation into candidates and the rules that the shared
sections do not state.

1. The seed is the situation and the naive question about it, in plain words. The record
   describes both in `field`, and it was opened with `start: {kind: "thought_experiment",
   description}`. A seed question is starting material, not a candidate by itself.
2. The interpretations. A mathematical interpretation states precisely what the seed question
   asks. For each interpretation, state its objects, the admissible motions or configurations,
   the quantifiers, the regularity or convexity assumptions, the quantity asked about (a minimum
   or an infimum, a measure or a dimension) and the subject class it lies in. A naive question
   admits several interpretations, and each asks a different thing; the needle gives at least
   three: the least area of a convex region in which the segment turns, the least area of an
   unrestricted such region, and the size of a static set that contains a unit segment in every
   direction. Record each interpretation as a form of a forming call ("Exploration") whose
   `question.kind` is `thought_experiment` and whose move is `reading`, an exact reading of a
   question that is not yet exact. Give the leading interpretation the purpose
   `expected_statement` and every other interpretation the purpose `alternative` of it.
3. The expectations. For each interpretation, write the expected answer as a precise proposition
   with its assumptions and its grounds, before any test; a vague impression is not an
   expectation until it is stated as such a proposition. Each expectation has one outcome:
   confirmed, refuted (a reported refutation included), supported or unresolved. The outcome is
   a fact about the expectation, not a calibration: calibrate each claim that the record makes
   about the expectation on its own, as the calibration paragraph of "The rubric" states, and
   treat a structural explanation without a proof as an experimental finding or a reasoned
   judgment, never as an established result. Record each expectation as a form of a forming
   call with the move `expectation`, with the purpose `expected_statement`, the other answers
   that you weighed as its alternatives, and the tests of step 4 as its planned checks. The
   inputs of that call cite the forming call of its interpretation.
4. Test each interpretation with the shared exploration tools, iterating as needed. An
   interpretation does not require its own solver attack. Record how each expectation ended
   with `form-outcome`. Its four outcomes are standings of its form: confirmed is `proved`,
   `implied_by_known_results` or `found_in_literature`, by what confirmed it; refuted is
   `refuted`; `supported` and `unresolved` keep their names. An interpretation is a question,
   and the outcome of its form says how that question stands and what the exploration did with
   it: `found_in_literature` when the works read settle it, `proved` when an accepted result of
   this record settles it, `supported` or `unresolved` otherwise, with its disposition.
5. The derived candidates. An interpretation ends settled (its expectation confirmed or refuted,
   by the literature or by this record), supported or unresolved, and candidates derive from each
   of these outcomes. A settled interpretation is material for derived questions: the structure
   of the examples that the result produced, the quantity it leaves open, the connection it
   reveals. An expectation need not be refuted. Record each derived question as a candidate of
   kind `thought_experiment`, and check every candidate of that kind against the rule of the kind
   when you compare the candidates or resume the exploration. The candidate's evidence cites the
   forming calls and the outcomes that it derives from, and each form that a candidate takes up
   gets an outcome with the disposition `taken_up_as_candidate`. A candidate that cites a
   forming call or a form outcome is a candidate formed in this record, so a slate that selects
   it cites a slate review ("Candidates and the slate", step 7).
6. The situation's naturalness or simplicity is not evidence of significance and earns no grade.
7. A settled seed question alone does not end the exploration. When the exploration ends,
   record its report ("The end of an exploration"): the report is due for every record that
   starts from a thought experiment.

A record that was opened before 0.83.0 keeps its interpretations and expectations as it holds
them. Record the interpretations and expectations that it states from now on as forms.

The absolute principle of "Exploration" and rubric item A7 also apply to expectations formed
during this exploration. A vague impression, an unsuccessful attempt or a change of model is not a
refutation. In the needle problem, the expected minimizer for unrestricted regions was the
deltoid, of area pi/8, and Besicovitch (1928) showed that the area can be made arbitrarily small.
A question about the size and structure of static measure-zero sets that contain a unit segment
in every direction asks about the world that the refutation reveals when that is its purpose, and
a change of the quantity asked about does not by itself make a question admissible; a question
that asks for the class of regions in which the deltoid stays minimal, with a value case that
depends on finding where the refuted expectation still holds, is of the excluded kind. The convex
interpretation is not of that kind: it was posed in 1917, before the refutation, and Pál confirmed
its expected answer, the equilateral triangle of unit height, in 1921.

Openness for an elementary situation follows the shared standard of "Openness", with searches
that cover both the plain description of the situation and its mathematical formulations:
alternative terminology, equivalent formulations, older problem literature, and the neighbouring
fields where the situation arises. The searches track the distinctions that decide whether an
interpretation is settled: the admissible objects and motions, the quantifiers, convexity or
regularity, dimension, measure, and a minimum against an infimum. Follow decisive citations under
the shared openness requirements. The searches are adaptive and bounded: no fixed number of
searches or exhaustive survey of all fields is required.

The rule of "Exploration" that one record holds the exploration applies with these particulars:

- The record was opened with `area` set to the subject class of the leading interpretation: the
  interpretation that was expected to be investigated most closely.
- When that rule permits a restart because the leading interpretation belongs to another class,
  apply the steps, the principle and the openness requirements of this section in the
  successor, and open the successor as "A new record that takes up a candidate of such a
  record" states.
- Unless you restart, keep the interpretations of every class, their expectations and their
  tests in this record. Record their derived candidates here and compare them on this record's
  slate. A selected derived candidate of another class moves to a record of its class through
  "Carry a reviewed candidate to another record", and its forms go with it through "The
  forming history of a candidate taken up in another record".

## An exploration from a phenomenon that appears in several places

A phenomenon that appears in several places is a fact, a pattern or a method that the works you
read state separately in several settings, each for reasons of its own. An exploration that
starts from one asks what one exact statement says about all of those places. The abc
conjecture is the model, in our reading of its history. It is one inequality about coprime
integers with a + b = c. Hall's conjecture with epsilon, the strong form of Szpiro's conjecture
and Fermat's last theorem for large exponents each follow from it, by applying it to a family
of triples or through a proved translation. It is the counterpart for integers of a proved
theorem on polynomials, and the translated inequality without epsilon is false. Such an
exploration is an exploration as "Exploration" describes it, with its budget, its tools, its
slate and its end, and a candidate derived from it meets the same thresholds as every
candidate. That a statement gathers others is no evidence for its answer, and the exploration
can end without a statement. This section gives the research that forms statements from the
phenomenon and the rules that the shared sections do not state.

The exploration looks for a statement for which the record can establish a gain over the list
of what it gathers. What it gathers includes statements or bodies of facts with separate
grounds: the works read state or study each of them for reasons of its own and present none of
them as a case, a refinement or a consequence of another. The gain has one of three forms, and
a proof, a full reading or a computation within its scope establishes it:

- A common structure. The gathered statements are each derived from one and the same assertion
  of the statement, by applying it to a family of its instances or through a proved
  translation. A statement from which each gathered statement follows through a clause of its
  own, however it is written, gathers them as a list.
- An explanatory relation. The statement determines facts of one body of knowledge from facts
  of another, and the record shows this on cases in which both were known independently.
- Another mathematical advantage that the record proves, such as a reduction under which a
  named line of proofs reaches results that it does not reach without the statement.

No statement has the gain by its form. A conjunction of known statements, a strengthening or a
generalization of one named statement, a variant of a statement in one parameter, and a
statement about an object that is defined so that it unpacks into known statements each need
the established gain, like any other statement. Whether a statement has the gain is decided
from the statement and its established relations, not from the aim of the exploration that
formed it.

1. The start. The record was opened with `start: {kind: "phenomenon", description}`. Its `area`
   is the subject class in which the statement was expected to lie, and its `field` describes
   the phenomenon and the question of what one exact statement says about its places. The
   phenomenon is starting material, not a candidate by itself.
2. The places. Survey where the phenomenon appears with `search`, `read` and `expand`, and with
   the population tool where the activity of a line matters. For each place, read in full the
   work that states or studies it for reasons of its own. The places with separate grounds are
   what a later statement can gather. Record the phenomenon and its places in the first forming
   call: its `question.kind` is `phenomenon`, and each entry of `question.places` describes one
   place and cites the evidence that shows it, in the evidence shapes of "Value and the
   rubric", step 6. Every call whose `question.kind` is `phenomenon` names at least one place;
   a later call can state a narrower question of its own under another kind.
3. The move. Form statements by the move that fits the material, and name it in `move.kind`:
   - `transfer`: a proved theorem of a neighbouring setting, carried to this setting by a stated
     dictionary. Read the theorem and its proof in full first. Give the `dictionary` of the
     call: the two settings and each term that is carried. A published heuristic or a
     prediction from outside mathematics is carried in the same way, and the grounds of its
     forms say that the derivation is conditional.
   - `restatement`: a conjecture that exists in a form that no instance checks, stated in a
     form that each instance checks.
   - `isolation`: the weakest hypothesis that repairs each of several proofs that fail at the
     same step, taken from your delivered solver attempts or from the works read.
   - `comparison`: data of two sources, compared. Give `comparison_count` and `amounts.pairs`,
     the number of comparisons made and of pairs compared, because an agreement found among
     many tries is weighed against their number.
   - `expectation`: an expected answer written as an exact proposition, for example the value
     that an invariant computed at scale suggests.
   - `correction`: a form changed after a check or an attack showed a fault (step 7).

   The kind is an open value. Record another move, for example a generalization or a
   dualization, in your own words.
4. The forms. State each form before its checks, in the forming call of the move that produced
   it, with its text, its purpose and its grounds. State beside it:
   - its nearest alternatives, which are the statements that could be posed in its place: a
     stronger form, a weaker form, the same assertion with another constant or exponent, or
     another assertion about the same objects. Each is a form of its own with the purpose
     `alternative`;
   - the relations that you expect of it: what it would imply, what it would follow from, and
     what it would be equivalent to or incompatible with. A relation stays expected until a
     proof or a full reading establishes it, and an expected relation supports no premise of a
     value case;
   - its planned checks (step 5).

   Give the numbers of forms, of alternatives and of expected relations in `amounts`.
5. The checks, with what each can show. A check compares a form with a fact, by a computation or
   a proof, on an instance, a family of instances, a consequence or an asymptotic regime. State
   for each planned check the outcome that would contradict the form (`contradiction`), or the
   reason that no outcome can (`no_contradiction_reason`). No finite set of triples contradicts
   the abc inequality, because its constant is chosen after epsilon, so a computed triple is an
   observation; a family of triples with unbounded ratio for one fixed epsilon would refute it.
   A check without a contradicting outcome gives an observation. The data that suggested a form
   are its origin: list each probe that a forming call used in `probes`, with the role `origin`
   for a form that was chosen with its result in view and `observation` otherwise. A probe
   recorded after a form is no origin of it (`problem_form_origin_late`), and no probe result
   earns credit as a test. A run earns that credit for the statement of the record, and only
   when its plan declared the test before the execution ("A run as a test of the statement").
   A run exists for the current statement only, so a form is tested in this sense once it is
   the statement of the record.
6. The attacks. Attack each form that you expect to pose, and its stronger forms, before
   anything is posed. Look for counterexamples with probes. With bounded solver objectives of
   their own ("The attempt", "Objectives beside the attempt"), try to prove that a stronger form
   fails, that the form implies a named statement or follows from one, or that it is equivalent
   to one. Record how each form, each expected relation and each planned check ended with
   `form-outcome`, with the evidence and its standing as "Exploration" states them. Keep every
   refutation in the record as a fact about mathematics, with its counterexample.
7. The corrections. When a check or an attack shows a fault in a form, record the corrected form
   with the move `correction`. Its `previous` names the earlier form and the reason for the
   change. Its `correction_checks` has one entry for each planned check of the earlier form:
   whether the check was made (`was_made`), whether the corrected form keeps its prediction
   (`keeps_prediction`), and whether the correction was chosen with its outcome in view
   (`chosen_with_outcome`). A check whose outcome shaped the correction is the origin of the
   corrected form. Record the other corrections that you compared as forms with the purpose
   `alternative`, and give the earlier form an outcome with the disposition `superseded` that
   names the corrected form. A correction of a form that the record held as its expected
   statement and that was refuted is a statement posed in place of a refuted one: form it so
   that it meets the three conditions of "Exploration", and enter the replacement part in the
   accounts of its value record ("The accounts"). When a run made a planned check of the
   earlier form, give the `execution_id` of that run in its `correction_checks` entry; a run
   stays a test of the corrected statement only through that entry ("A run as a test of the
   statement").
8. The derived candidates. Candidates derive from every outcome. A form that stands after its
   attacks is a candidate question. A refuted form is material for questions about what the
   refutation reveals, under the absolute principle. A form that was proved, or that known
   results imply, is a result and no open problem; what it gives can be a foothold of a
   neighbouring question. A form found in the literature belongs to its source. Record each
   derived question as a candidate of kind `phenomenon` whose evidence cites the forming calls
   and the outcomes that it derives from, and give each form that a candidate takes up an
   outcome with the disposition `taken_up_as_candidate` and the candidate's id. A slate that
   selects such a candidate cites a slate review, and the slate gives priority by the expected
   gain (section "Rules of the shared sections for these records" of this document).
9. The end. Give every form an outcome: `set_aside` with the reason for a form that you leave,
   and `not_examined` with the means that were lacking for a form that you could not examine.
   When the exploration ends, record its report with `problem-report` ("The end of an
   exploration"), and give in your summary the forms that it produced and how each ended, and
   for each posted statement whether the record established a gain. When a slate selects
   nothing, the search goes on, as "The search after a slate that selects nothing" of the skill
   states.

The absolute principle of "Exploration" and rubric item A7 apply to every form of this
exploration. The public text of a problem that comes from it states what the record
established, and it makes no comparison with a named conjecture as a claim about the standing
of the statement.

The places of a phenomenon often lie in several subject classes. The record was opened in the
class in which the statement was expected to lie. Name as a further area each other class in
which a statement of the record would have a consequence, an earlier statement or a nearer
alternative, or in which the phenomenon has a place ("Further areas").

The forming records stay in the record that made them. When a restart or a carry under the
rule of "Exploration" takes a formed candidate to another record, bring its forming history
there with `forming-import`, and record the transfer in this record ("The forming history of a
candidate taken up in another record"). Open the other record as "A new record that takes up a
candidate of such a record" states. The imported records keep their origin, their dates and
their standing, so the destination states no form of this record again. A record that stated
the forms of another record again under the text of 0.83.0 or 0.84.0 keeps those forming calls
as it holds them.
