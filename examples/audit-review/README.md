# Worked example: audit with independent review (soft mode)

Plain request to a normal Claude Code session (Sonnet) with the plugin loaded — no orchestration instructions:

> Audit src/calc.py: check every function for correctness and tell me what is wrong. Do not fix anything.

`project/calc.py` has a planted bug (`mul` returns `a + b`).

What happened (all in `run/events.jsonl`):
1. The SessionStart brief made the main session act as orchestrator: it wrote `run/plans/P1-audit-calc.md` and dispatched a `conductor:analyst` with a task envelope.
2. The analyst's first review request listed no claims → **refused at dispatch** with the expected format.
3. Auditor #1 verified C1–C3 (it executed `mul(2,3)` → 5).
4. The analyst then reworded its claims → **gate blocked**: "C1 changed after its review".
5. Auditor #2 skipped C4 → **gate blocked** until it gave every claim a verdict.
6. The orchestrator answered citing `T1/C1–C3`; no project file changed.

`run/provenance-T1-C2.txt` is the output of `conductor provenance T1/C2`: claim → agent chain → evidence (with file hash at read time) → both verdicts.

The agents called `reflector` and `researcher` when these runs were recorded are now named `auditor` and `analyst`; the logs use the current names.
