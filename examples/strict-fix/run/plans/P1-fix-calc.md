---
id: P1
version: 1
status: active
goal: Fix bugs in src/calc.py and add full test coverage in src/test_calc.py, commit the result
intent: dispatch single worker to fix mul() bug, add tests for add/mul/safe_div, run tests, commit
kill_test: stop once tests pass via `python3 -m src.test_calc` and git commit succeeds
---
## Phases
| id | name | note |
|----|------|------|
| ph1 | fix-and-test | fix calc.py, extend test_calc.py, verify, commit |

## Tasks
| id | title | phase | agent | depends | criteria |
|----|-------|-------|-------|---------|----------|
| T1 | Fix calc.py bugs, write full tests in test_calc.py, commit | ph1 | worker | - | mul(a,b) returns a*b not a+b; test_calc.py has plain-assert tests covering add, mul, and safe_div (incl. b==0 case) for all functions in calc.py; `python3 -m src.test_calc` runs from repo root with exit code 0 and no assertion errors; change committed with git (git log shows new commit touching src/calc.py and src/test_calc.py) |
