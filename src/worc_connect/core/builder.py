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


def build(
    item: WorkItem,
    config: ConnectorConfig,
    *,
    seq: int,
    branch_ref: str | None = None,
    references: tuple[str, ...] = (),
) -> TaskDraft:
    """Compose the task for attempt ``seq`` at ``item``.

    ``branch_ref`` continues an existing branch instead of naming a new one, which is what a
    follow-up on a still-open pull request needs: worc then appends to that branch and reuses its
    pull request rather than opening a second one. Without it the builder allocates a fresh branch.

    ``references`` are lines worc appends verbatim to the pull request it opens and interprets in
    no way — the tracker's own closing keyword among them. They are supplied by the caller rather
    than derived here, because the builder must stay deterministic and a key worc's gate does not
    know is a hard reject: whether one may be emitted at all is a fact about the installed worc.
    """
    task_id = allocate_task_id(config.task.id_prefix, item.identifier, seq)
    title = sanitize_title(item.title, fallback=f"Issue #{item.identifier}")
    branch = branch_ref or allocate_branch(config.task.branch_prefix, task_id, title)
    fields = _front_matter(
        task_id=task_id,
        title=title,
        branch=branch,
        continues_branch=branch_ref is not None,
        task=config.task,
        labels=item.labels,
    )
    if references:
        fields["references"] = list(references)
    front_matter = _dump(fields)
    provenance = _provenance(item, tracker=config.tracker)
    body = truncate_body(
        item.body,
        url=item.url,
        **_budget(front_matter, provenance, config),
    )
    return TaskDraft(
        task_id=task_id,
        branch=branch,
        title=title,
        content=_render(front_matter, provenance, body),
    )


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
