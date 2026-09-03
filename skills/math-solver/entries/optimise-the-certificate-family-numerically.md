---
name: optimise-the-certificate-family-numerically
move_class: compute
costs: [bound_quality, obligations]
---

## Trigger

- choose-the-auxiliary-weight-or-certificate has produced a family of admissible auxiliary objects, and the bound is a functional over the family that a finite-dimensional truncation approximates: a linear or semidefinite program, a ratio of quadratic forms, a generalised eigenvalue problem.
- A known construction in the other direction gives a value to compare against.

## Action

1. Truncate the family to a finite-dimensional ansatz and solve the optimisation as a numerical optimisation run across the whole parameter range (every dimension, every tuple size, every admissible exponent).
2. Where the numerical bound touches the known construction to many digits, conjecture exactness there and name those cases as the targets; where it stays visibly above, record the gap as the family's ceiling at that parameter.
3. Compute the optimiser to high precision and extract its structural data by a symbolic computation run: its roots, its sign pattern, the rational or algebraic coefficients with the certificate that identifies them, the constants at the interfaces.
4. Split the argument into components with numerical interfaces and optimise them in parallel (split-into-components-with-explicit-interfaces).
5. Convert the numerical optimum into a proof: a certified special-case check of the bound at the optimiser, or a formal check of the closed form suggested by the data (certify-the-finite-residue-by-computation).

## Output form

The numerical optimum over the family at each parameter, the cases where it is conjecturally sharp, and structural data on the optimiser.

## Failure signal

The optimum converges to a value that cannot be identified (not rational, not a recognisable constant) and shows no structure; or the truncated problem's optimum keeps improving with the truncation dimension without converging; or the numerical optimum meets the known construction nowhere.

## Typical cash-out

Computational evidence; quantitative improvement (the numerical bound made rigorous); problem paper (the exactness conjecture).
