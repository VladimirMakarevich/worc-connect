"""The gate: which items an explicit rule admits, and which it refuses.

The whole security posture of the connector rests on this table, so it is asserted item by item
rather than through the loop that calls it.
"""

from __future__ import annotations

import pytest

from support import work_item
from worc_connect.config import GateConfig
from worc_connect.core.gate import GateReason, evaluate


def gate(
    *,
    labels: tuple[str, ...] = (),
    authors: tuple[str, ...] = (),
    allow_all: bool = False,
) -> GateConfig:
    return GateConfig(labels=labels, authors=authors, allow_all=allow_all)


def test_a_trigger_label_admits_an_item() -> None:
    verdict = evaluate(work_item(labels=("bug", "worc")), gate(labels=("worc",)))

    assert verdict.admitted
    assert verdict.reason is GateReason.TRIGGER_LABEL


def test_an_item_without_the_trigger_label_is_refused() -> None:
    verdict = evaluate(work_item(labels=("bug",)), gate(labels=("worc",)))

    assert not verdict.admitted
    assert verdict.reason is GateReason.NO_TRIGGER_LABEL


def test_an_item_with_no_labels_at_all_is_refused() -> None:
    assert not evaluate(work_item(labels=()), gate(labels=("worc",))).admitted


def test_a_label_matches_whatever_case_a_maintainer_applied_it_in() -> None:
    assert evaluate(work_item(labels=("Worc",)), gate(labels=("worc",))).admitted


def test_an_author_allow_list_narrows_a_label_rule() -> None:
    admitted = gate(labels=("worc",), authors=("maintainer",))

    assert evaluate(work_item(labels=("worc",), author="maintainer"), admitted).admitted
    refused = evaluate(work_item(labels=("worc",), author="stranger"), admitted)
    assert not refused.admitted
    assert refused.reason is GateReason.AUTHOR_NOT_ALLOWED


def test_an_author_allow_list_alone_admits_by_author() -> None:
    verdict = evaluate(work_item(labels=(), author="maintainer"), gate(authors=("maintainer",)))

    assert verdict.admitted
    assert verdict.reason is GateReason.ALLOWED_AUTHOR


def test_an_author_matches_whatever_case_the_tracker_reports() -> None:
    assert evaluate(
        work_item(labels=(), author="Maintainer"), gate(authors=("maintainer",))
    ).admitted


def test_an_item_whose_author_the_tracker_cannot_name_is_refused_by_an_allow_list() -> None:
    assert not evaluate(work_item(labels=(), author=""), gate(authors=("maintainer",))).admitted


def test_allow_all_admits_an_item_no_rule_mentions() -> None:
    verdict = evaluate(work_item(labels=()), gate(allow_all=True))

    assert verdict.admitted
    assert verdict.reason is GateReason.ALLOW_ALL


@pytest.mark.parametrize("reason", list(GateReason))
def test_every_reason_is_a_fixed_token_an_item_cannot_influence(reason: GateReason) -> None:
    assert reason.isprintable()
    assert " " not in str(reason)
