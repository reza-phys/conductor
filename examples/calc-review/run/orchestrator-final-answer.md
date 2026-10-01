## Coverage gaps — calc module (`src/calc.py`)

- **`sub(a, b)`** (line 5) — **completely untested**, zero references in `src/test_calc.py`.
- **`add(a, b)`** (line 1) — tested, but only one trivial case (`add(2, 2) == 4`); missing negative, zero, float, and distinct-operand cases.

Test runner note: `src/test_calc.py` is a plain assert script, not pytest-based (pytest isn't installed); run via `python3 -m src.test_calc` from repo root, exit 0.

No files were edited by any agent.
