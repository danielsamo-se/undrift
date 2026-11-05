from __future__ import annotations

from datetime import date

import pytest

import fixtures
import workflow
from store import Store


def test_seed_migrates_the_four_preflight_documents(store: Store) -> None:
    docs = {d.id: d for d in store.documents()}
    assert docs == fixtures.DOCUMENTS
    assert {c["id"] for c in store.channels()} == {d.channel for d in docs.values()}
    policy = store.policy(fixtures.POLICY_ID)
    assert (policy.version, policy.voluntary_return_days) == (1, 60)
    assert [e.type for e in store.events()] == ["store.seeded"]


def test_init_is_idempotent(store: Store) -> None:
    assert store.init() is False
    assert len(store.documents()) == 4
    assert len(store.events(type_prefix="store.seeded")) == 1


def test_state_persists_across_store_instances(changed: Store) -> None:
    reopened = Store(changed.path)
    policy = reopened.policy(fixtures.POLICY_ID)
    assert policy.version == 2
    assert policy.voluntary_return_days == 30
    assert policy.effective_from == date(2026, 11, 4)
    assert [p.voluntary_return_days for p in reopened.policy_history(fixtures.POLICY_ID)] == [60, 30]
    assert reopened.policy(fixtures.POLICY_ID, version=1).voluntary_return_days == 60
    assert reopened.document("doc-001").version == 3


def test_policy_change_records_event_with_versions(changed: Store) -> None:
    created = changed.events(type_prefix="policy.version_created")
    assert len(created) == 1
    e = created[0]
    assert e.actor == "reviewer:test-reviewer"
    assert (e.subject_type, e.subject_id) == ("policy", fixtures.POLICY_ID)
    assert e.metadata == {
        "policy_id": fixtures.POLICY_ID, "version": 2, "previous_version": 1,
        "voluntary_return_days": 30, "previous_voluntary_return_days": 60,
        "effective_from": "2026-11-04"}


def test_identical_policy_change_is_rejected(changed: Store, token: str) -> None:
    with pytest.raises(workflow.Conflict):
        workflow.change_policy(changed, fixtures.POLICY_ID, token=token,
                               **fixtures.SHIFTLY_RETURNS_CHANGE)
    assert changed.policy(fixtures.POLICY_ID).version == 2


def test_policy_change_requires_review_token(store: Store, token: str) -> None:
    with pytest.raises(workflow.Unauthorized):
        workflow.change_policy(store, fixtures.POLICY_ID, token="agent-says-it-is-allowed",
                               **fixtures.SHIFTLY_RETURNS_CHANGE)
    assert store.policy(fixtures.POLICY_ID).version == 1
    denied = store.events(type_prefix="auth.denied")
    assert denied[-1].status == "denied" and denied[-1].metadata == {"action": "policy.change"}
