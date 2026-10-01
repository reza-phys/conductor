# Worked example: strict mode fix + commit

`claude -p --agent conductor:conductor --plugin-dir <conductor>` on the same buggy `calc.py`:

> Make src/calc.py correct and well tested: fix any bugs, add tests covering every function, and commit the change with git.

- The orchestrator ran with exactly the strict tool set (no Bash, project edits refused) and wrote a valid plan.
- One worker fixed `mul`, extended the tests, ran them, and committed.
- An auditor re-ran the tests and verified T1/C1–C4.
- The worker first cited its *reviewer's verdicts* as evidence (`claim:review:T1/C1`) — the gate blocked that and it cited the files and commands instead.
- Finding from this run: the commit was made with `git -c user.email=… -c … commit`, which the first trailer regex missed. Fixed afterwards (`_GIT_COMMIT` now accepts any git global options; regression test added), so this example's commit has no `Conductor-*` trailers.

The agents called `reflector` and `researcher` when these runs were recorded are now named `auditor` and `analyst`; the logs use the current names.
