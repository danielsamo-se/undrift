"""Exercise the real REST adapter with an in-memory GitHub HTTP transport.

No GitHub/Vercel credentials or remote mutations are used by these tests.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

import api
from conftest import propose, GOOD_FAQ
from publishing import GitHub, Publisher, Target, verify_site
import workflow


def sha(value):
    return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Remote:
    """Small deterministic GitHub protocol fixture with real trees/parents/refs."""
    def __init__(self, original='All customers can return items within 60 days.'):
        self.repo = 'example/shiftly'
        self.path = 'content/returns.txt'
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.refs = {}
        self.prs = []
        self.calls = []
        self.lost_pr_response = False
        self.deployed = False
        self.deployment_state = 'success'
        self.seed(original)

    def blob(self, text):
        key = sha(text)
        self.blobs[key] = text
        return key

    def seed(self, original):
        tree = [{'path': self.path, 'mode': '100644', 'type': 'blob', 'sha': self.blob(original)},
                {'path': 'index.html', 'mode': '100644', 'type': 'blob', 'sha': self.blob('<main>Shop</main>')}]
        tid = sha(tree); self.trees[tid] = tree
        self.base = sha({'tree': tid, 'parents': []})
        self.commits[self.base] = {'sha': self.base, 'tree': {'sha': tid}, 'parents': []}
        self.refs['main'] = self.base

    def merged(self, content=None):
        p = self.prs[0]
        tree = self.commits[p['head']['sha']]['tree']['sha']
        if content is not None:
            entries = [dict(e) for e in self.trees[tree]]
            next(e for e in entries if e['path'] == self.path)['sha'] = self.blob(content)
            tree = sha(entries); self.trees[tree] = entries
        merge = sha({'tree': tree, 'merge': p['head']['sha']})
        self.commits[merge] = {'sha': merge, 'tree': {'sha': tree}, 'parents': [{'sha': self.base}, {'sha': p['head']['sha']}]}
        p.update(merged=True, merged_at='2026-11-06T00:00:00Z', state='closed', merge_commit_sha=merge)
        self.refs['main'] = merge
        return merge

    def handle(self, request):
        route = request.url.path.removeprefix('/repos/' + self.repo + '/')
        method = request.method
        data = json.loads(request.content) if request.content else {}
        self.calls.append((method, route))
        def response(value, status=200):
            return httpx.Response(status, json=value)
        if route.startswith('git/ref/heads/'):
            branch = route.removeprefix('git/ref/heads/')
            return response({'object': {'sha': self.refs[branch]}}) if branch in self.refs else response({}, 404)
        if route == 'git/refs':
            branch = data['ref'].removeprefix('refs/heads/')
            if branch in self.refs: return response({}, 422)
            self.refs[branch] = data['sha']
            return response({'object': {'sha': data['sha']}}, 201)
        if route == 'git/trees':
            tree = [dict(e) for e in self.trees[data['base_tree']]]
            for new in data['tree']:
                entry = dict(new); entry['sha'] = self.blob(entry.pop('content'))
                tree = [e for e in tree if e['path'] != entry['path']] + [entry]
            tid = sha(sorted(tree, key=lambda e: e['path']))
            self.trees[tid] = tree
            return response({'sha': tid}, 201)
        if route.startswith('git/trees/'):
            return response({'tree': self.trees[route.split('/')[-1]], 'truncated': False})
        if route == 'git/commits':
            cid = sha(data)
            self.commits[cid] = {'sha': cid, 'tree': {'sha': data['tree']}, 'parents': [{'sha': p} for p in data['parents']]}
            return response(self.commits[cid], 201)
        if route.startswith('git/commits/'):
            return response(self.commits[route.split('/')[-1]])
        if route.startswith('git/blobs/'):
            text = self.blobs[route.split('/')[-1]].encode()
            return response({'encoding': 'base64', 'size': len(text), 'content': base64.b64encode(text).decode()})
        if route == 'pulls' and method == 'GET':
            return response(self.prs)
        if route == 'pulls' and method == 'POST':
            p = {'number': 1, 'state': 'open', 'merged': False,
                 'head': {'sha': self.refs[data['head']], 'ref': data['head'], 'repo': {'full_name': self.repo}},
                 'base': {'ref': data['base'], 'repo': {'full_name': self.repo}}}
            self.prs.append(p)
            if self.lost_pr_response:
                raise httpx.ReadError('Response lost after creation')
            return response(p, 201)
        if route == 'pulls/1': return response(self.prs[0])
        if route == 'deployments':
            return response([{'id': 42, 'sha': self.refs['main'], 'production_environment': True}] if self.deployed else [])
        if route == 'deployments/42/statuses': return response([{'state': self.deployment_state}])
        if route.startswith('commits/') and route.endswith('/status'):
            return response({'state': 'success', 'statuses': [{'context': 'Vercel', 'state': 'success'}]})
        raise AssertionError((method, route))


@pytest.fixture
def setup(changed, token, tmp_path):
    proposal = propose(changed)
    workflow.approve(changed, proposal.id, token=token)
    target = Target('example/shiftly', site_url='https://shiftly-demo.vercel.app')
    remote = Remote()
    client = httpx.Client(base_url='https://api.github.com', transport=httpx.MockTransport(remote.handle))
    # Every production request is isolated, including before deployment succeeds.
    web = site_client(commit='old')
    service = Publisher(changed, target, tmp_path / 'publishing.db', GitHub(target, client), web)
    yield service, proposal, remote
    client.close()
    web.close()


def publish(setup, token):
    service, p, _ = setup
    plan = service.plan(p.id, token)
    return service.publish(p.id, plan['digest'], token)


def test_one_file_exact_commit_and_idempotent_pr(setup, token):
    service, p, remote = setup
    state = publish(setup, token)
    retry = service.publish(p.id, state['digest'], token)
    assert state['pr'] == retry['pr']
    assert len(remote.prs) == 1
    assert remote.refs['main'] == remote.base  # no merge, no write to main
    commit = state['pr']['commit']
    assert service.github.file(commit)['content'] == p.proposed_content
    new = remote.trees[remote.commits[commit]['tree']['sha']]
    old = remote.trees[remote.commits[remote.base]['tree']['sha']]
    assert next(e for e in new if e['path'] == 'index.html') == next(e for e in old if e['path'] == 'index.html')
    assert [e['type'] for e in retry['events']] == ['publishing.approved', 'publishing.pr_created']


@pytest.mark.parametrize('reason', ['rejected', 'pending', 'document', 'policy'])
def test_stale_or_unapproved_proposals_cannot_publish(setup, token, reason):
    service, p, remote = setup
    if reason == 'rejected': workflow.reject(service.store, p.id, token=token)
    if reason == 'pending':
        with service.store.write() as c: c.execute("UPDATE proposals SET status='pending_review' WHERE id=?", (p.id,))
    if reason == 'document':
        from test_workflow import edit_document_externally
        edit_document_externally(service.store, p.document_id, 'Changed elsewhere.')
    if reason == 'policy':
        import fixtures
        workflow.change_policy(service.store, p.policy_id, token=token,
            **(fixtures.SHIFTLY_RETURNS_CHANGE | {'voluntary_return_days': 21}))
    with pytest.raises(workflow.Conflict): service.plan(p.id, token)
    assert not remote.calls


def test_exact_authorization_and_repository_staleness(setup, token):
    service, p, remote = setup
    plan = service.plan(p.id, token)
    with pytest.raises(workflow.Conflict): service.publish(p.id, 'f'*64, token)
    remote.seed('Different live policy.')
    with pytest.raises(workflow.Conflict, match='differs'): service.publish(p.id, plan['digest'], token)
    assert not any(method == 'POST' for method, _ in remote.calls)


@pytest.mark.parametrize('action', ['plan', 'publish', 'refresh'])
def test_unauthorized_never_calls_github(setup, action):
    service, p, remote = setup
    with pytest.raises(workflow.Unauthorized):
        getattr(service, action)(p.id, *(['f'*64] if action == 'publish' else []), token='bad')
    assert not remote.calls


def test_concurrent_publish_creates_one_pr(setup, token):
    service, p, remote = setup
    plan = service.plan(p.id, token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.publish(p.id, plan['digest'], token), range(2)))
    assert results[0]['pr'] == results[1]['pr'] and len(remote.prs) == 1


def test_lost_pr_response_recovers_without_duplicate(setup, token):
    service, p, remote = setup
    remote.lost_pr_response = True
    assert publish(setup, token)['status'] == 'pr_created'
    assert len(remote.prs) == 1


def test_existing_merged_pr_recovers_after_deleted_branch(setup, token):
    service, p, remote = setup
    state = publish(setup, token)
    plan = service.plan(p.id, token)
    remote.merged()
    del remote.refs[plan['head_branch']]
    # Simulate a crash after the remote operation, before the journal recorded it.
    with service.write() as c:
        c.execute('UPDATE publications SET state=?', (json.dumps({'status': 'approved'}),))
    retry = service.publish(p.id, plan['digest'], token)
    assert retry['pr'] == state['pr'] and len(remote.prs) == 1
    assert plan['head_branch'] not in remote.refs


def test_unrelated_base_commit_movement_invalidates_human_preview(setup, token):
    service, p, remote = setup
    plan = service.plan(p.id, token)
    # Same content, different parent commit: still requires explicit reauthorization.
    remote.commits['new-base'] = remote.commits[remote.base]
    remote.refs['main'] = 'new-base'
    with pytest.raises(workflow.Conflict, match='plan changed'):
        service.publish(p.id, plan['digest'], token)
    assert not remote.prs


def test_branch_hijack_is_refused_without_force_push(setup, token):
    service, p, remote = setup
    plan = service.plan(p.id, token)
    remote.refs[plan['head_branch']] = remote.base
    with pytest.raises(workflow.Conflict, match='unapproved'): service.publish(p.id, plan['digest'], token)
    assert not remote.prs
    assert service.get(p.id)['error']
    assert not any(method == 'PATCH' for method, _ in remote.calls)


def test_modified_pr_or_merge_cannot_be_verified(setup, token):
    service, p, remote = setup
    publish(setup, token)
    remote.prs[0]['head']['sha'] = remote.base
    with pytest.raises(workflow.Conflict, match='identity'): service.refresh(p.id, token)


def site_client(content=GOOD_FAQ, commit=None, page_content=None, status=200, metadata=None, html=None):
    def handler(request):
        if request.url.path == '/build.json':
            return httpx.Response(status, json={'commit': commit} if metadata is None else metadata)
        if request.url.path == '/content/returns.txt': return httpx.Response(status, content=content.encode())
        from html import escape
        return httpx.Response(status, text=html if html is not None else
            '<p id="returns-policy">' + escape(content if page_content is None else page_content) + '</p>')
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_distinct_merge_deploy_and_public_verification(setup, token):
    service, p, remote = setup
    publish(setup, token)
    assert service.refresh(p.id, token)['status'] == 'pr_created'
    merged = remote.merged()
    assert service.refresh(p.id, token)['status'] == 'merged'
    remote.deployed = True
    service.web = site_client(commit='old')
    state = service.refresh(p.id, token)
    assert state['status'] == 'merged' and state['error']
    assert 'publishing.deployed' not in {e['type'] for e in state['events']}
    service.web.close()
    service.web = site_client(commit=merged)
    state = service.refresh(p.id, token)
    assert state['status'] == 'verified' and state['merge_commit'] == merged
    assert {'publishing.merged', 'publishing.deployed', 'publishing.verified'} <= {e['type'] for e in state['events']}
    service.web.close()
    assert service.store.proposal(p.id).status == 'approved'  # no local apply


def test_verified_status_is_withdrawn_after_production_rollback(setup, token):
    service, p, remote = setup
    publish(setup, token)
    merged = remote.merged(); remote.deployed = True
    with site_client(commit=merged) as web:
        service.web = web
        assert service.refresh(p.id, token)['status'] == 'verified'
    with site_client(commit='old') as web:
        service.web = web
        state = service.refresh(p.id, token)
    assert state['status'] == 'merged' and 'verified_at' not in state and state['error']
    assert 'verification_evidence' not in state and 'content_sha256' not in state


def test_production_site_verifies_without_github_deployment_records(setup, token):
    service, p, remote = setup
    publish(setup, token)
    merged = remote.merged()
    after_publish = len(remote.calls)
    with site_client(commit=merged) as web:
        service.web = web
        state = service.refresh(p.id, token)
        repeated = service.refresh(p.id, token)
    assert state['status'] == repeated['status'] == 'verified'
    assert state['merge_commit'] == merged
    assert 'deployment_id' not in state and 'github_deployment' not in state
    evidence = state['verification_evidence']
    assert evidence['source'] == 'production_site'
    assert evidence['site_url'] == service.target.site_url
    assert evidence['expected_merge_commit'] == evidence['observed_build_commit'] == merged
    expected_hash = hashlib.sha256(p.proposed_content.encode()).hexdigest()
    assert evidence['approved_content_sha256'] == evidence['raw_policy_sha256'] == evidence['rendered_policy_sha256'] == expected_hash
    assert evidence['policy_element_count'] == 1
    assert evidence['checked_at'] == state['verified_at']
    for stage in ['merged', 'deployed', 'verified']:
        events = [e for e in repeated['events'] if e['type'] == 'publishing.' + stage]
        assert len(events) == 1
        if stage in {'deployed', 'verified'}:
            assert events[0]['metadata']['verification_evidence']['source'] == 'production_site'
    assert all(method == 'GET' for method, _ in remote.calls[after_publish:])
    assert service.store.proposal(p.id).status == 'approved'


@pytest.mark.parametrize('kwargs', [
    {'commit': 'old'}, {'content': GOOD_FAQ + '\n'}, {'page_content': 'Incorrect 30-day policy.'},
    {'status': 401}, {'status': 302}, {'status': 503}, {'metadata': []}, {'metadata': 'unexpected'},
    {'html': '<p>No policy element</p>'},
    {'html': '<p id="returns-policy">a</p><p id="returns-policy">b</p>'},
    {'html': '<p id="returns-policy"><span id="returns-policy">' + GOOD_FAQ + '</span></p>'},
    {'html': '<![invalid]><p id="returns-policy">' + GOOD_FAQ + '</p>'},
])
def test_no_deployment_records_cannot_bypass_production_checks(setup, token, kwargs):
    service, p, remote = setup
    publish(setup, token)
    merged = remote.merged()
    # A successful provider status cannot rescue incorrect or unavailable production content.
    assert service.github.request('GET', f'commits/{merged}/status')['state'] == 'success'
    with site_client(**({'commit': merged} | kwargs)) as web:
        service.web = web
        state = service.refresh(p.id, token)
    assert state['status'] == 'merged' and state['error']
    assert all(k not in state for k in ['verified_at', 'content_sha256', 'verification_evidence'])
    assert not {'publishing.deployed', 'publishing.verified'} & {e['type'] for e in state['events']}


def test_production_timeout_without_deployment_records_stays_unverified(setup, token):
    service, p, remote = setup
    publish(setup, token); remote.merged()
    def timeout(request):
        raise httpx.ReadTimeout('synthetic timeout')
    with httpx.Client(transport=httpx.MockTransport(timeout)) as web:
        service.web = web
        state = service.refresh(p.id, token)
    assert state['status'] == 'merged' and state['error']
    assert 'verified_at' not in state


@pytest.mark.parametrize('deployment_state', ['failure', 'error', 'pending', 'queued', 'in_progress', 'inactive', None])
def test_unsuccessful_production_deployment_fails_closed(setup, token, deployment_state):
    service, p, remote = setup
    publish(setup, token)
    merged = remote.merged(); remote.deployed = True; remote.deployment_state = deployment_state
    with site_client(commit=merged) as web:
        service.web = web
        state = service.refresh(p.id, token)
    assert state['status'] == 'merged' and state['error']
    assert state['github_deployment']['id'] == 42
    assert state['github_deployment']['state'] == deployment_state
    assert 'verified_at' not in state and 'verification_evidence' not in state


def test_newer_failed_deployment_cannot_be_hidden_by_older_success(setup, token):
    service, p, remote = setup
    publish(setup, token)
    merged = remote.merged()
    original = remote.handle
    def provider(request):
        if request.url.path.endswith('/deployments'):
            return httpx.Response(200, json=[
                {'id': 43, 'sha': merged, 'production_environment': True},
                {'id': 42, 'sha': merged, 'production_environment': True}])
        if request.url.path.endswith('/deployments/43/statuses'):
            return httpx.Response(200, json=[{'state': 'failure'}])
        return original(request)
    with httpx.Client(base_url='https://api.github.com', transport=httpx.MockTransport(provider)) as github, site_client(commit=merged) as web:
        service.github.client = github
        service.web = web
        state = service.refresh(p.id, token)
    assert state['status'] == 'merged' and state['error']
    assert state['github_deployment']['id'] == 43
    assert state['github_deployment']['state'] == 'failure'
    assert 'verified_at' not in state


def test_verified_production_proof_is_revoked_on_changed_pr_head(setup, token):
    service, p, remote = setup
    publish(setup, token)
    merged = remote.merged()
    with site_client(commit=merged) as web:
        service.web = web
        assert service.refresh(p.id, token)['status'] == 'verified'
        remote.prs[0]['head']['sha'] = remote.base
        with pytest.raises(workflow.Conflict, match='identity'):
            service.refresh(p.id, token)
    state = service.get(p.id)
    assert state['status'] == 'blocked'
    assert all(k not in state for k in ['verified_at', 'content_sha256', 'verification_evidence', 'site_url'])


@pytest.mark.parametrize('rollback', [{'commit': 'old'}, {'content': 'All customers can return items within 60 days.'},
                                    {'page_content': 'All customers can return items within 60 days.'}])
def test_rollback_without_deployment_records_revokes_verification(setup, token, rollback):
    service, p, remote = setup
    publish(setup, token)
    merged = remote.merged()
    with site_client(commit=merged) as web:
        service.web = web
        assert service.refresh(p.id, token)['status'] == 'verified'
    with site_client(**({'commit': merged} | rollback)) as web:
        service.web = web
        state = service.refresh(p.id, token)
    assert state['status'] == 'merged' and state['error']
    assert all(k not in state for k in ['verified_at', 'content_sha256', 'verification_evidence'])


def test_preview_deployment_cannot_establish_production_readiness(setup, token):
    service, p, remote = setup
    publish(setup, token); remote.merged(); remote.deployed = True
    handle = remote.handle
    def preview(request):
        if request.url.path.endswith('/deployments'):
            return httpx.Response(200, json=[{'id': 42, 'sha': remote.refs['main'], 'production_environment': False, 'environment': 'Preview'}])
        return handle(request)
    service.github.client = httpx.Client(base_url='https://api.github.com', transport=httpx.MockTransport(preview))
    assert service.refresh(p.id, token)['status'] == 'merged'
    service.github.client.close()


def test_provider_errors_cannot_echo_credentials(setup, token, caplog):
    service, p, _ = setup
    secret = 'test-only-github-credential-never-log'
    client = httpx.Client(base_url='https://api.github.com', headers={'Authorization': 'Bearer '+secret},
        transport=httpx.MockTransport(lambda r: httpx.Response(403, json={'message': secret})))
    service.github.client = client
    with pytest.raises(workflow.WorkflowError) as error:
        service.plan(p.id, token)
    assert secret not in str(error.value) and secret not in caplog.text
    client.close()


@pytest.mark.parametrize('kwargs', [
    {'content': 'Old 60 days.'}, {'page_content': 'Old 60 days.'}, {'commit': 'old'}, {'status': 302},
])
def test_public_site_requires_exact_bytes_rendered_policy_and_commit(setup, token, kwargs):
    service, p, _ = setup
    plan = service.plan(p.id, token)
    with site_client(**({'commit': 'merged'} | kwargs)) as web:
        with pytest.raises(workflow.WorkflowError): verify_site(service.target, plan, 'merged', web)


def test_wrong_merged_content_is_blocked(setup, token):
    service, p, remote = setup
    publish(setup, token); remote.merged('Unreviewed text')
    with pytest.raises(workflow.Conflict, match='Merged content'): service.refresh(p.id, token)


def test_review_api_requires_token_and_explicit_digest(setup, token):
    service, p, remote = setup
    client = TestClient(api.build_app(service.store, ui_dir=None, publisher=service),
        base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    base = f'/api/proposals/{p.id}/publication'
    assert client.get(base).json()['configured']
    for action in ['plan', 'publish', 'refresh']:
        assert client.post(base+'/'+action, json={}).status_code == 401
    headers = {'Authorization': 'Bearer '+token}
    plan = client.post(base+'/plan', json={}, headers=headers).json()['plan']
    assert client.post(base+'/publish', json={}, headers=headers).status_code == 400
    assert client.post(base+'/publish', json={'approval_digest': plan['digest']}, headers=headers).status_code == 200
    assert client.get(base).json()['publication']['status'] == 'pr_created'


def test_review_api_vertical_slice_through_merge_deployment_and_verification(setup, token):
    service, p, remote = setup
    client = TestClient(api.build_app(service.store, ui_dir=None, publisher=service),
        base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    base = f'/api/proposals/{p.id}/publication'
    headers = {'Authorization': 'Bearer '+token}
    plan = client.post(base+'/plan', json={}, headers=headers).json()['plan']
    result = client.post(base+'/publish', json={'approval_digest': plan['digest']}, headers=headers)
    assert result.status_code == 200 and result.json()['publication']['status'] == 'pr_created'
    assert client.post(base+'/refresh', json={}, headers=headers).json()['publication']['status'] == 'pr_created'
    commit = remote.merged()  # Simulated human merge, never an adapter operation.
    assert client.post(base+'/refresh', json={}, headers=headers).json()['publication']['status'] == 'merged'
    remote.deployed = True  # Vercel/GitHub callback represented by deployment metadata.
    with site_client(commit=commit) as web:
        service.web = web
        assert client.post(base+'/refresh', json={}, headers=headers).json()['publication']['status'] == 'verified'
    final = client.get(base).json()['publication']
    assert final['merge_commit'] == commit and final['content_sha256'] == hashlib.sha256(GOOD_FAQ.encode()).hexdigest()
    assert not any('/merge' in route for _, route in remote.calls)


@pytest.mark.parametrize('url', ['http://localhost:8000', 'https://127.0.0.1', 'https://foo.vercel.app.evil.test', 'https://foo.vercel.app/path'])
def test_verification_url_is_operator_scoped(url):
    with pytest.raises(workflow.WorkflowError): Target('a/b', site_url=url)


def test_target_changes_invalidate_existing_authorization(setup, token):
    service, p, _ = setup
    state = publish(setup, token)
    service.target = Target('other/repository')
    with pytest.raises(workflow.Conflict): service.publish(p.id, state['digest'], token)


def test_mcp_does_not_expose_publishing_credentials_or_tools():
    import server
    assert 'publishing' not in Path('server.py').read_text()
    assert not hasattr(server, 'publish')
