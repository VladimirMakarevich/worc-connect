# Follow-ups — decisions taken deliberately, to be revisited

Small things this repository chose to leave open, with the reasoning that made leaving them open the right call at the time. Each entry names what was decided, what it costs, and what would make it worth changing. This is not a bug list: an entry here describes behaviour that works as intended today.

An entry leaves this page when the decision is revisited — either because the cost came due, or because it was made permanent somewhere that binds (a rule in [.agents/rules/](../../.agents/rules/), a requirement in a design record).

## FU-1 — A triage verdict's text is published on the item unchanged

**Decided:** 2026-09-11, with phase 07.

**What happens.** When triage ends an item at `needs-info`, `duplicate` or `declined`, the connector posts a comment carrying the `reason` from the triage report exactly as the triage agent wrote it. Nothing is stripped, escaped, shortened or otherwise transformed.

**Why.** The person who has to act on a `needs-info` question is the reporter reading the item, and a paraphrase would be a question nobody can answer. The feature is worth nothing without the text.

**What it costs.** The triage agent read the item, and the item was written by a stranger. So a prompt injection in an issue can, through that agent, choose text this connector then posts as a comment under the operator's own account. Concretely, such a comment can contain `@mentions` that notify people, `#123` references that create cross-links on other issues, and arbitrary Markdown and links. The comment travels as a body **file**, so it reaches no argument list and the rest of the untrusted-text boundary is intact; what is published is the text, and only the text.

**Why not bounded now.** Bounds were considered and deliberately not added: the operator's call was to build the mechanic freely first and fit restrictions to the situations that actually arise, rather than to guess at them. Restricting later is cheap — the text passes through one function — while a bound guessed wrong would quietly make the round trip useless.

**What would make it worth changing.** A repository where drive-by issues are common; a comment that actually notified people nobody meant to notify; or a decision to run triage on a public repository whose issues anybody can open. The likely shape then is the least restrictive thing that removes the specific harm — neutralizing `@` and `#`, capping the length, keeping the text in a blockquote — rather than dropping the text.

**Where it lives.** `_comment_body` in `src/worc_connect/core/writeback.py`, and the two module docstrings there and in `src/worc_connect/core/triage.py` that state the choice.

**Revisited:** 2026-09-11, by the [local-research](local-research/README.md) record (D13, FR-R10a): the operator chose the least restrictive shape this entry anticipated — one `reason` paragraph, capped, blockquoted, with `@` and `#` defused — applied to **both** report producers through the one `_comment_body`, and extended to the `actionable` verdict so the reporter is also told their issue was reproduced. This entry leaves the page when that record's phase 04 lands; until then the behaviour above is what ships.

## FU-2 — A failed task is watched for `worc rerun` for seven days, not for ever

**Decided:** 2026-11-18, fixing F-8 of the second real run ([real-run-plan.md](tracker-connector/real-run-plan.md)).

**What happens.** A row worc ended `failed` or `manual_action_required`, with no pull request, is checked against `worc list --format json --all` on every tick for `RERUN_WINDOW` — seven days after the failure. Once worc lists the task as queued, running or done again, the item is read by identifier and followed to the end, a closed item included. After the window the row is no longer checked, and a quiet tick launches no worc at all again.

**Why.** Following every failure for ever would cost one `worc list` per tick for the life of the repository, for failures nobody will rerun. A week covers an operator who looks at a failure after a weekend.

**What it costs.** A rerun later than seven days after the failure is not seen: the item keeps `worc:failed` after the rerun's pull request merges, and the operator fixes the label by hand (the operations guide says so).

**What would make it worth changing.** A late rerun that went unseen in practice, or worc gaining a cheaper signal than its listing for "this task is running again". The window is one constant in `src/worc_connect/core/reconcile.py`; making it a configuration key is the likely next step.

**Where it lives.** `RERUN_WINDOW` and `awaits_rerun` in `src/worc_connect/core/reconcile.py`, used by `src/worc_connect/core/follow.py`.
