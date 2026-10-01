**Audit result (verified, T1/C1-C3), no changes made:**

1. **`add`** (line 1-2) — correct. `return a + b`.
2. **`mul`** (line 5-6) — **bug**: returns `a + b` instead of `a * b` (source even has a comment flagging it). `mul(2, 3)` → `5` instead of `6`.
3. **`safe_div`** (line 9-12) — correct. Returns `None` on `b == 0`, otherwise `a / b`; handles negatives fine. Non-numeric input raises `TypeError`, but that's outside its stated scope, not a logic bug.
