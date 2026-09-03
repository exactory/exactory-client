---
name: condition-on-the-one-missing-input
move_class: reduce
costs: [axioms, bound_quality, object, obligations]
---

## Trigger

- The strongest known method, run to the end, leaves exactly one unproved input: an error term uniform in a parameter, an equidistribution estimate beyond the proven range, a finiteness statement about an auxiliary class, an algebraic hypothesis on an auxiliary finite group, a hardness hypothesis.
- The missing input is a recognised conjecture, a named hypothesis, or a uniform version of a known theorem.

## Action

1. State the implication "missing input implies target" as a theorem with the sharpest threshold: how much of the hypothesis (which exponent, which range of the parameter) suffices.
2. Trace which restricted form of the hypothesis the argument actually uses (a restriction of the parameter to a structured subset, to a finite list, to a residue class) and restate the requirement in that weaker form before anyone has proved the full hypothesis.
3. Look for a setting where the hypothesis is a theorem (an analogue over a function field, a finite-field model, an enlarged language) and prove the result there.
4. Attack the restricted hypothesis directly, trading generality for what current estimates reach.
5. When the hypothesis can be replaced by an explicit object that does its work (an explicit annihilator in place of a triviality assumption), do so and drop the hypothesis.

## Output form

A conditional theorem with an explicit threshold, and a restricted hypothesis weaker than the named one.

## Failure signal

The input needed is equivalent to the target; or the implication reaches only a weaker conclusion than the target even under the full hypothesis; or the restricted form still needs a strength of estimate that no current technique gives and no analogue setting provides.

## Typical cash-out

Conditional result; reduction or equivalence; special case in the analogue setting.
