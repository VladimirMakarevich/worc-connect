"""The task builder: one gated work item in, one task file worc accepts unchanged out.

This is the only place a task file is composed, and it is deterministic by design — the point of
the whole connector is that a model may propose and only code decides. Everything an item
contributes is bounded here: its title through the sanitizer, its body verbatim under a provenance
line, its identifier in the id and the branch, its URL in the provenance and in a truncation marker.
Nothing else it wrote reaches the file, and nothing it wrote reaches anywhere but the file.

The front matter carries a key **only when the operator configured it**. That is what keeps the
connector from having an opinion where worc already has a default, and it is why the emitted key set
is a strict subset of worc's allowed set — never ``nodes``, ``subtasks``, ``decomposition``,
``trust_level`` or ``prompt_audit``, none of which a connector has any business setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import yaml

from worc_connect.config import ConnectorConfig, TaskConfig
from worc_connect.core.items import WorkItem
from worc_connect.core.naming import allocate_branch, allocate_task_id
from worc_connect.core.sanitize import sanitize_title, truncate_body
from worc_connect.core.triage import FailingTest, Report

# Every front-matter key this builder can emit. A strict subset of worc's ``ALLOWED_TASK_KEYS``,
# and deliberately none of the keys that decide how a task is *run* — a task file the connector
# writes cannot carry extra arguments, a permission profile or a flow edit, and the set is named
# here so a test can hold it to that.
ALLOWED_KEYS: Final = frozenset(
    {
        "id",
        "title",
        "branch_name",
        "branch_mode",
        "branch_ref",
        "task_type",
        "queue",
        "priority",
        "commit_type",
        "auto_merge",
        "references",
    }
)

# The heading worc's gate requires a non-empty section under.
_DESCRIPTION_HEADING: Final = "## Description"

# The headings a report's own contributions are published under. ``Acceptance criteria`` is worc's
# own: it is the section worc looks for to decide a task needs no refinement pass, so criteria a
# triage run proposed are worth exactly what they are worth under that name and nothing is invented
# when a report carries none. The other is the connector's, fixed so a reader — and a diff — finds
# the reproduction in the same place every time.
_ACCEPTANCE_HEADING: Final = "## Acceptance criteria"
_FAILING_TEST_HEADING: Final = "## Failing test"

# The priority a triage task carries. Stated rather than configured because it is the whole reason
# the step is tolerable: a triage task is short and every implementation task behind it waits on it,
# so it must not queue behind the long runs it exists to decide the shape of.
_RESEARCH_PRIORITY: Final = "high"


@dataclass(frozen=True)
class TaskDraft:
    """One composed task: the names the connector allocated and the file it will write.

    ``branch`` is kept next to the text rather than re-derived from it, because it is what the pull
    request is later found by — the row stores this value, and one spelling of a branch name is the
    only way that search can be trusted.
    """

    task_id: str
    branch: str
    title: str
    content: str


@dataclass(frozen=True)
class _Spec:
    """One composition, as the three public entry points differ from each other.

    A record rather than six keyword arguments threaded through the renderer: the entry points are
    the readable surface, and the thing they actually vary is small enough to name once.
    """

    seq: int
    branch_ref: str | None = None
    references: tuple[str, ...] = ()
    #: overrides of the configured dispatch fields, for a task whose kind the connector decides
    #: rather than the operator — today only the triage task, which names its flow and its priority.
    dispatch: dict[str, Any] | None = None
    #: the body, when it is not the item's own text: what a triage report proposed, already
    #: assembled by the code that read it.
    body: str | None = None


def build(
    item: WorkItem,
    config: ConnectorConfig,
    *,
    seq: int,
    branch_ref: str | None = None,
    references: tuple[str, ...] = (),
) -> TaskDraft:
    """Compose the implementation task for attempt ``seq`` at ``item``.

    ``branch_ref`` continues an existing branch instead of naming a new one, which is what a
    follow-up on a still-open pull request needs: worc then appends to that branch and reuses its
    pull request rather than opening a second one. Without it the builder allocates a fresh branch.

    ``references`` are lines worc appends verbatim to the pull request it opens and interprets in
    no way — the tracker's own closing keyword among them. They are supplied by the caller rather
    than derived here, because the builder must stay deterministic and a key worc's gate does not
    know is a hard reject: whether one may be emitted at all is a fact about the installed worc.
    """
    return _compose(item, config, _Spec(seq=seq, branch_ref=branch_ref, references=references))


def build_research(item: WorkItem, config: ConnectorConfig, *, seq: int) -> TaskDraft:
    """Compose the triage task that analyses ``item`` before any implementation task exists.

    It differs from an implementation task in exactly two dispatch fields — the flow it names and
    its priority — and carries no closing reference, because the flow it names publishes nothing
    and a pull request it will never open cannot close anything.
    """
    return _compose(
        item,
        config,
        _Spec(
            seq=seq,
            dispatch={"task_type": config.research.flow, "priority": _RESEARCH_PRIORITY},
        ),
    )


def build_from_report(
    item: WorkItem,
    config: ConnectorConfig,
    report: Report,
    *,
    seq: int,
    branch_ref: str | None = None,
    references: tuple[str, ...] = (),
) -> TaskDraft:
    """Compose the implementation task a triage run concluded ``item`` deserves.

    The same composition as :func:`build` — the same front matter, the same limits, the same
    provenance line — with the body assembled from the report instead of from the item's own text.
    That convergence is the point: what an implementation task looks like must not depend on
    whether an agent analysed the item first, and the sections the report contributes are chosen
    here, by code, rather than by whatever the agent decided to write.
    """
    return _compose(
        item,
        config,
        _Spec(
            seq=seq,
            branch_ref=branch_ref,
            references=references,
            body=_report_body(report),
        ),
    )


def _compose(item: WorkItem, config: ConnectorConfig, spec: _Spec) -> TaskDraft:
    """The one renderer every task goes through, whatever produced its body."""
    task_id = allocate_task_id(config.task.id_prefix, item.identifier, spec.seq)
    title = sanitize_title(item.title, fallback=f"Issue #{item.identifier}")
    branch = spec.branch_ref or allocate_branch(config.task.branch_prefix, task_id, title)
    fields = _front_matter(
        task_id=task_id,
        title=title,
        branch=branch,
        continues_branch=spec.branch_ref is not None,
        task=config.task,
        labels=item.labels,
    )
    fields.update(spec.dispatch or {})
    if spec.references:
        fields["references"] = list(spec.references)
    front_matter = _dump(fields)
    provenance = _provenance(item, tracker=config.tracker)
    body = truncate_body(
        item.body if spec.body is None else spec.body,
        url=item.url,
        **_budget(front_matter, provenance, config),
    )
    return TaskDraft(
        task_id=task_id,
        branch=branch,
        title=title,
        content=_render(front_matter, provenance, body),
    )


def _report_body(report: Report) -> str:
    """What a triage report contributes to the task worc will run, and in which order.

    The reason leads because it is the ask; the criteria and the reproduction follow under headings
    a reader and worc both recognise. Ordered so that the truncation the limits may force cuts the
    reproduction — the longest and the most re-derivable part — before it cuts what the task is for.
    """
    sections = [report.reason]
    if report.acceptance_criteria:
        criteria = "\n".join(f"- {line}" for line in report.acceptance_criteria)
        sections.append(f"{_ACCEPTANCE_HEADING}\n\n{criteria}")
    if report.failing_test is not None:
        sections.append(_failing_test_section(report.failing_test))
    return "\n\n".join(section for section in sections if section)


def _failing_test_section(failing_test: FailingTest) -> str:
    """The reproduction, fenced so the task file carries it as text and never as a file to run."""
    location = "" if failing_test.path is None else f"Suggested path: `{failing_test.path}`\n\n"
    return f"{_FAILING_TEST_HEADING}\n\n{location}```\n{failing_test.body.rstrip()}\n```"


def _render(front_matter: str, provenance: str, body: str) -> str:
    """The task file as worc reads it: front matter, then a non-empty description section."""
    return f"---\n{front_matter}---\n\n{_DESCRIPTION_HEADING}\n\n{provenance}\n\n{body}\n"


def _budget(front_matter: str, provenance: str, config: ConnectorConfig) -> dict[str, int]:
    """How much room the item's own text has left inside worc's three limits.

    worc measures the whole file, so the front matter and the provenance line are spent before the
    body gets any budget at all. Measuring the rendered document with an empty body — rather than
    estimating — is what keeps this correct when the rendering changes.
    """
    overhead = _render(front_matter, provenance, "")
    return {
        "max_bytes": max(config.worc.max_task_bytes - len(overhead.encode("utf-8")), 0),
        "max_lines": max(config.worc.max_task_lines - len(overhead.splitlines()), 0),
        "max_line_bytes": config.worc.max_line_bytes,
    }


def _provenance(item: WorkItem, *, tracker: str) -> str:
    """The one line that says where the task came from, in the vocabulary the core speaks.

    No acceptance criteria are synthesized under it: an item that carries none produces a task that
    carries none, because enriching a thin report is worc's refinement step, not the connector's
    guess at what the reporter meant.
    """
    author = f" by @{item.author}" if item.author else ""
    return f"Source: {tracker} item #{item.identifier}{author} — {item.url}"


def _front_matter(
    *,
    task_id: str,
    title: str,
    branch: str,
    continues_branch: bool,
    task: TaskConfig,
    labels: tuple[str, ...],
) -> dict[str, Any]:
    """The front-matter fields, carrying only the keys the operator actually configured.

    Returned as a mapping rather than as text so the caller can add the contract keys it decides
    about without this function growing an argument for each of them.
    """
    fields: dict[str, Any] = {"id": task_id, "title": title}
    if continues_branch:
        # worc's own pairing rule: `existing` is the mode that requires a ref, and a `branch_ref`
        # outside that mode is a contradiction its gate rejects.
        fields["branch_mode"] = "existing"
        fields["branch_ref"] = branch
    else:
        fields["branch_name"] = branch
    fields.update(_dispatch_fields(task, labels))
    return fields


def _dump(fields: dict[str, Any]) -> str:
    """The front-matter block as worc parses it: insertion order kept, one value per line."""
    return yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, width=10**6)


def _dispatch_fields(task: TaskConfig, labels: tuple[str, ...]) -> dict[str, Any]:
    """The configured dispatch keys, with the per-label commit type applied where one matches."""
    configured = (
        ("task_type", task.task_type),
        ("queue", task.queue),
        ("priority", task.priority),
        ("commit_type", _commit_type(task, labels)),
        ("auto_merge", task.auto_merge),
    )
    return {key: value for key, value in configured if value is not None}


def _commit_type(task: TaskConfig, labels: tuple[str, ...]) -> str | None:
    """The commit type for an item: the first matching label's override, else the configured one.

    Item order decides, not mapping order, so the same item and the same configuration always
    produce the same task — and the operator can read the answer off the item's own label list.
    """
    overrides = {name.casefold(): value for name, value in task.commit_type_by_label.items()}
    for label in labels:
        override = overrides.get(label.casefold())
        if override is not None:
            return override
    return task.commit_type
