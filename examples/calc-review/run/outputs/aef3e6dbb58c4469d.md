Confirmed by direct reading:

- `src/calc.py` has exactly two functions: `add(a, b)` (line 1-2, returns `a + b`) and `sub(a, b)` (line 5-6, returns `a - b`).
- `src/test_calc.py` (3 lines) is: `from src.calc import add` / `assert add(2, 2) == 4`.

This is a simple, self-contained check — no need to delegate sub-tasks.

Findings per function:

**`sub(a, b)` — completely untested.** There is no reference to `sub` anywhere in `src/test_calc.py` (no import, no call, no assertion). This function has zero test coverage.

**`add(a, b)` — tested, but inadequately.** It is imported and exercised once: `assert add(2, 2) == 4`. Gaps:
- Only one input pair is checked (2, 2). No variation in operand values at all.
- No negative-number case (e.g., `add(-2, 3)` or `add(-1, -1)`).
- No zero case (e.g., `add(0, 0)` or `add(5, 0)`).
- No float case (e.g., `add(1.5, 2.5)`).
- No non-commutative-order check — because the two arguments are equal (2 and 2), the test can't distinguish `a + b` from a hypothetical bug like `a - b + something` or swapped-argument errors; a case like `add(2, 3) == 5` (and ideally `add(3, 2) == 5` to confirm symmetry) would be needed to catch argument-order bugs, though since `+` is commutative this is a lower-severity gap than for `sub`.

Summary: `sub` is the function with no coverage at all. `add` has coverage but only a single trivial, symmetric-operand case, missing negative numbers, zero, floats, and distinct-operand ordering checks.

<conductor-report task="T3.1" status="done">
summary: Read src/calc.py (add at lines 1-2, sub at lines 5-6) and src/test_calc.py (3-line script) directly, confirming the given context exactly. Determined: (1) sub(a, b) is completely untested — no import, call, or assertion referencing it anywhere in src/test_calc.py. (2) add(a, b) is tested but only with a single case, assert add(2, 2) == 4 — adequacy gaps identified: no negative-number input, no zero input, no float input, and no distinct-operand ordering check (both operands are the same value, 2, so it can't catch argument-order mistakes).
files: none (read-only assessment, no files created or edited)
needs: none
</conductor-report>