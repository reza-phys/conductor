---
id: P1
version: 1
status: active
goal: Review the calc module and its tests
intent: Find gaps in calc.py and its test coverage, without changing code
kill_test: Stop if the test suite cannot run at all
---
## Phases
| id | name | note |
|---|---|---|
| PH1 | Inspect | read code and run tests |
| PH2 | Assess | judge coverage |

## Tasks
| id | title | phase | agent | depends | criteria |
|---|---|---|---|---|---|
| T1 | Inventory functions | PH1 | worker | - | every function listed with line numbers |
| T2 | Run the tests | PH1 | worker | - | exact command + exit code |
| T3 | Coverage assessment | PH2 | worker | T1, T2 | each untested function named |
