Confirmed mul(2,3) returns 5 (bug), and safe_div behaves as described.

Findings:

- Read the full file at src/calc.py (12 lines, 3 functions: add, mul, safe_div — confirmed via `cat -n`, `wc -l`, and `grep -c "^def "`).
- C1 (add): lines 1-2 match exactly `def add(a, b): / return a + b`. No bug. Verified.
- C2 (mul): lines 5-6 match exactly, including the inline comment `# bug: should be a * b`. Ran the function logic directly: `mul(2,3)` evaluates to 5, not 6. Verified.
- C3 (safe_div): lines 9-12 match exactly; `b == 0` guard returns None, otherwise returns `a / b`. Ran it: `safe_div(6,0)` → None, `safe_div(6,3)` → 2.0. The claim's design note (non-numeric inputs raise unhandled TypeError, by-design not a logic bug) is accurate and appropriately hedged. Verified.
- C4: function count/list is correct (exactly add, mul, safe_div, nothing else — confirmed via grep), but the line-count assertion is wrong. `wc -l`, `cat -n`, and `python splitlines()` all agree the file has 12 lines, not 13. Refuted on the numeric claim (though the "exactly three functions, no others" part is itself true).

Criteria coverage: the task required, for every function, name/line/issue-or-"no issues"/example input. C1-C3 jointly cover all three functions in the file with correct line citations and a concrete bug example for mul — this satisfies the core task criteria. C4 is extraneous meta-information (not required by the task) and contains an incorrect reproducible number, which fails the standards.md bar ("numbers are reproducible").

<conductor-report task="review:T1" status="done">
summary: 3 verified, 1 refuted, 0 unverified; criteria coverage: all three functions in the file (add, mul, safe_div) are addressed with correct line citations and bug evidence, satisfying the per-function audit requirement; the extra file-size claim (C4) contains an incorrect line count.
verdicts:
- C1: verified | Read lines 1-2: `def add(a, b): return a + b`. Matches claim exactly, no bug.
- C2: verified | Read lines 5-6: `def mul(a, b): return a + b  # bug: should be a * b`. Executed mul(2,3) → 5 (expected 6 if multiplying). Comment and bug confirmed.
- C3: verified | Read lines 9-12: guards `b == 0` returning None, else `a / b`. Executed safe_div(6,0) → None, safe_div(6,3) → 2.0. Matches claim; TypeError-on-non-numeric note is accurate and correctly not classified as a bug.
- C4: refuted | Function count (3: add, mul, safe_div, no others) is correct per `grep -c "^def "`, but the "13 lines total" assertion is wrong — `wc -l`, `cat -n`, and Python `splitlines()` all show the file has 12 lines.
needs: none
</conductor-report>