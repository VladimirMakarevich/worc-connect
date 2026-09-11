# Backlog — worc-connect

The task queue of this repository. Two items: the connector half of the `tracker-connector` design record, copied from the orchestrator repository's backlog, and `local-research`, a next-version follow-up to its optional triage path; the rules in [.agents/rules/](../../.agents/rules/) restate the invariants of the first. Beside them, [follow-ups.md](follow-ups.md) records decisions taken deliberately and meant to be revisited — behaviour that works as intended today and whose cost is written down where it is paid.

| Item | Summary | Status |
| --- | --- | --- |
| [tracker-connector/](tracker-connector/README.md) | The connector's copy of the `tracker-connector` design record: problem, requirements, design, acceptance criteria, definition of done, decisions, and the implementation phases 03–07 that build the connector here (phases 01, 02, 08 are worc-side and live in the orchestrator repository). | ready-to-implement |
| [local-research/](local-research/README.md) | A second producer for the triage report: the connector runs the research agent itself, in a disposable worktree of a clone it owns, so the orchestrator's queue is not spent on triage. Removes the "No agent runtime" invariant deliberately; phases 01–05, all in this repository. | ready-to-implement |
