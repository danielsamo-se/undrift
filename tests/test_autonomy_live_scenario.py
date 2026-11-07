"""The isolated live autonomy scenario builder (no network)."""
import sys
from pathlib import Path

import pytest

import autonomy
import workflow as w

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import create_autonomy_live_demo as live  # noqa: E402


def test_scenario_is_isolated_labelled_and_delegated_before_any_run(tmp_path, token):
    store, delegation = live.create(tmp_path / 'live.db', token=token)
    assert store.metadata('scenario') == 'autonomy-live-v1' and store.metadata('seed_sha256') == live.seed_hash()
    assert store.document_ids() == ['help-101', 'help-102', 'web-101'] and not store.runs()
    assert (delegation['mode'], delegation['version'], delegation['max_per_run']) == ('bounded_auto', 1, 1)
    assert [c.document_id for c in w.detect_candidates(store, 'shiftly-returns') if c.actionable] == ['help-101']
    with store.read() as c:
        assert autonomy.policy(store, 'website', c)['effective_mode'] == 'pull_request'
    with pytest.raises(FileExistsError):
        live.create(tmp_path / 'live.db', token=token)


def test_scenario_delegation_requires_the_reviewer_token(tmp_path, token):
    with pytest.raises(w.Unauthorized):
        live.create(tmp_path / 'other.db', token='wrong-token-' + 'x' * 32)
