"""Human-authorized GitHub publishing. Never imported or exposed by the MCP server.

The separate journal avoids migrating application databases. Network mutations only
occur through publish(), after a reviewer authorizes a digest of an immutable plan.
No endpoint in this module merges a PR or initiates a Vercel deployment.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote, urlparse

import httpx

import config
from store import Store, now
import workflow

VERIFICATION_FIELDS = ('verified_at', 'content_sha256', 'site_url', 'verification_evidence')
JOURNAL_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS publications (database_id TEXT NOT NULL, proposal_id TEXT NOT NULL, '
    'plan TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(database_id, proposal_id))',
    'CREATE TABLE IF NOT EXISTS publication_events (seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, '
    'database_id TEXT NOT NULL, proposal_id TEXT NOT NULL, type TEXT NOT NULL, metadata TEXT NOT NULL)',
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class Target:
    repository: str
    branch: str = 'main'
    path: str = 'content/returns.txt'
    document_id: str = 'doc-001'
    site_url: str = ''

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', self.repository):
            raise workflow.WorkflowError('Configure an owner/repository publishing target')
        if not re.fullmatch(r'[A-Za-z0-9_/-]+', self.branch) or '..' in self.branch:
            raise workflow.WorkflowError('Invalid publishing base branch')
        if (not re.fullmatch(r'[A-Za-z0-9_./-]+', self.path) or self.path.startswith('/')
                or any(part in {'', '.', '..'} for part in self.path.split('/'))
                or self.path.startswith(('.github/', '.git/'))):
            raise workflow.WorkflowError('Invalid publishing content path')
        if self.site_url:
            u = urlparse(self.site_url)
            # Deliberately narrow until custom-domain validation is implemented.
            if (u.scheme != 'https' or not u.hostname or not u.hostname.endswith('.vercel.app')
                    or u.username or u.password or u.port or u.path not in {'', '/'} or u.query or u.fragment):
                raise workflow.WorkflowError('Use a production https://PROJECT.vercel.app origin')

    @classmethod
    def configured(cls):
        repo = os.environ.get('UNDRIFT_GITHUB_REPOSITORY', '')
        if not repo:
            return None
        return cls(repo, os.environ.get('UNDRIFT_GITHUB_BASE_BRANCH', 'main'),
                   os.environ.get('UNDRIFT_GITHUB_CONTENT_PATH', 'content/returns.txt'),
                   os.environ.get('UNDRIFT_PUBLISH_DOCUMENT_ID', 'doc-001'),
                   os.environ.get('UNDRIFT_STOREFRONT_URL', '').rstrip('/'))


class GitHub:
    """Real GitHub REST adapter. Credentials are read only by this API-side adapter."""
    def __init__(self, target: Target, client=None):
        self.target = target
        self.client = client

    def request(self, method, path, **kwargs):
        client = self.client
        owned = client is None
        if owned:
            credential_file = os.environ.get('UNDRIFT_PUBLISH_CREDENTIAL_FILE', '')
            if not credential_file:
                raise workflow.WorkflowError('Publishing credential file is not configured')
            try:
                token = json.loads(Path(credential_file).read_text())['github_token']
                if not isinstance(token, str) or not token.strip():
                    raise ValueError()
            except (OSError, ValueError, KeyError, TypeError):
                raise workflow.WorkflowError('Publishing credentials could not be loaded') from None
            client = httpx.Client(base_url='https://api.github.com', timeout=20,
                follow_redirects=False, trust_env=False, headers={
                    'Authorization': 'Bearer ' + token,
                    'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        try:
            response = client.request(method, f'/repos/{self.target.repository}/{path}', **kwargs)
            if response.status_code == 404:
                return None
            if response.status_code >= 300:
                raise workflow.WorkflowError('GitHub request refused', http_status=response.status_code)
            return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError):
            raise workflow.WorkflowError('GitHub request failed; retry safely using the same authorization') from None
        finally:
            if owned:
                client.close()

    def commit(self, sha):
        result = self.request('GET', 'git/commits/' + quote(sha, safe=''))
        if not result:
            raise workflow.Conflict('Repository commit not found')
        return result

    def ref(self, branch):
        return self.request('GET', 'git/ref/heads/' + quote(branch, safe='/'))

    def file(self, commit_sha):
        commit = self.commit(commit_sha)
        tree = self.request('GET', 'git/trees/' + commit['tree']['sha'], params={'recursive': '1'})
        if not tree or tree.get('truncated'):
            raise workflow.Conflict('Cannot verify the complete repository tree')
        entry = next((e for e in tree['tree'] if e['path'] == self.target.path), None)
        if not entry or entry['type'] != 'blob' or entry['mode'] != '100644':
            raise workflow.Conflict('Target must be a regular UTF-8 content file')
        blob = self.request('GET', 'git/blobs/' + entry['sha'])
        if not blob or blob.get('encoding') != 'base64' or blob.get('size', 0) > workflow.MAX_CONTENT * 4:
            raise workflow.Conflict('Unsupported repository content')
        try:
            content = base64.b64decode(blob['content'].replace('\n', ''), validate=True).decode('utf-8')
        except (ValueError, UnicodeError):
            raise workflow.Conflict('Target content is not valid UTF-8') from None
        return {'commit': commit_sha, 'tree': commit['tree']['sha'], 'blob': entry['sha'], 'content': content}

    def snapshot(self):
        ref = self.ref(self.target.branch)
        if not ref:
            raise workflow.Conflict('Publishing base branch does not exist')
        return self.file(ref['object']['sha'])

    def ensure_pr(self, plan):
        branch = plan['head_branch']
        owner = self.target.repository.split('/')[0]
        params = {'state': 'all', 'head': owner + ':' + branch, 'base': self.target.branch, 'per_page': 100}
        matches = self.request('GET', 'pulls', params=params) or []
        current = self.ref(self.target.branch)
        if not matches and (not current or current['object']['sha'] != plan['base_commit']):
            raise workflow.Conflict('Base branch advanced; authorize a fresh repository plan')
        tree = self.request('POST', 'git/trees', json={'base_tree': plan['base_tree'], 'tree': [{
            'path': self.target.path, 'mode': '100644', 'type': 'blob', 'content': plan['proposed_content']}]})
        if not tree:
            raise workflow.WorkflowError('Could not create the GitHub tree')
        ref = self.ref(branch)
        if ref is None and not matches:
            commit = self.request('POST', 'git/commits', json={
                'message': f"Undrift: reviewed correction {plan['proposal_id']}\n\nAuthorization: {plan['digest']}",
                'tree': tree['sha'], 'parents': [plan['base_commit']]})
            if not commit:
                raise workflow.WorkflowError('Could not create the GitHub commit')
            self.request('POST', 'git/refs', json={'ref': 'refs/heads/' + branch, 'sha': commit['sha']})
            ref = self.ref(branch)
        if not ref and not matches:
            raise workflow.Conflict('Publishing branch was not created')
        # A merged PR may have had its branch deleted. Recover its authorized
        # commit without recreating the branch or opening a replacement PR.
        commit_sha = matches[0]['head']['sha'] if matches else ref['object']['sha']
        commit = self.commit(commit_sha)
        if (commit['tree']['sha'] != tree['sha'] or [p['sha'] for p in commit['parents']] != [plan['base_commit']]):
            raise workflow.Conflict('Publishing branch contains an unapproved change; never force-pushing')
        if self.file(commit_sha)['content'] != plan['proposed_content']:
            raise workflow.Conflict('Publishing branch does not contain the exact approved content')
        if not matches:
            try:
                pr = self.request('POST', 'pulls', json={
                    'title': f"Correct Shiftly returns policy ({plan['proposal_id']})",
                    'head': branch, 'base': self.target.branch,
                    'body': f"Human-reviewed Undrift correction.\n\nProposal: `{plan['proposal_id']}`\n"
                            f"Authorization: `{plan['digest']}`\n\nOnly `{self.target.path}` changes. "
                            'Review and merge manually. No automatic merge is enabled.'})
                matches = [pr] if pr else []
            except workflow.WorkflowError:
                # A response may be lost after GitHub creates the PR.
                matches = self.request('GET', 'pulls', params=params) or []
                if not matches:
                    raise
        if len(matches) != 1:
            raise workflow.Conflict('Expected exactly one publishing pull request')
        pr = matches[0]
        self.check_pr(pr, plan, commit_sha)
        if pr['state'] != 'open' and not pr.get('merged_at'):
            raise workflow.Conflict('The publishing PR was closed without merging')
        return {'number': pr['number'], 'url': f"https://github.com/{self.target.repository}/pull/{pr['number']}",
                'commit': commit_sha}

    def check_pr(self, pr, plan, head_sha):
        if (pr['head']['sha'] != head_sha or pr['head']['ref'] != plan['head_branch']
                or pr['base']['ref'] != self.target.branch
                or pr['head']['repo']['full_name'].lower() != self.target.repository.lower()
                or pr['base']['repo']['full_name'].lower() != self.target.repository.lower()):
            raise workflow.Conflict('Pull request identity or content changed after authorization')

    def progress(self, plan, pr):
        remote = self.request('GET', f"pulls/{pr['number']}")
        if not remote:
            raise workflow.Conflict('Publishing PR is no longer accessible')
        self.check_pr(remote, plan, pr['commit'])
        if not remote.get('merged'):
            if remote['state'] == 'closed':
                raise workflow.Conflict('Pull request was closed without merging')
            return {'status': 'pr_created'}
        merge_sha = remote.get('merge_commit_sha')
        if not merge_sha or self.file(merge_sha)['content'] != plan['proposed_content']:
            raise workflow.Conflict('Merged content differs from the reviewed correction')
        result = {'status': 'merged', 'merge_commit': merge_sha}
        deployments = self.request('GET', 'deployments', params={'sha': merge_sha, 'per_page': 100}) or []
        # GitHub returns deployments newest first. An older successful deployment
        # must not override a newer failed/pending production deployment.
        for deployment in deployments:
            if deployment.get('sha') != merge_sha or not (deployment.get('production_environment')
                    or deployment.get('environment', '').lower() == 'production'):
                continue
            statuses = self.request('GET', f"deployments/{deployment['id']}/statuses", params={'per_page': 1}) or []
            result['deployment_id'] = deployment['id']
            result['github_deployment'] = {
                'source': 'github_deployment', 'id': deployment['id'], 'commit': merge_sha,
                'state': statuses[0].get('state') if statuses else None,
                'environment': deployment.get('environment', 'Production'),
            }
            break
        # Provider records are supplemental. Only independently observed exact
        # production output can establish deployed/verified publication status.
        return result


class PolicyText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []
        self.matches = 0

    def handle_starttag(self, tag, attrs):
        policy_element = dict(attrs).get('id') == 'returns-policy'
        if policy_element:
            self.matches += 1
        if self.depth:
            self.depth += 1
        elif policy_element:
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def verify_site(target, plan, merge_sha, client=None):
    if not target.site_url:
        raise workflow.WorkflowError('Production storefront URL is not configured')
    owned = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False, trust_env=False)
    try:
        responses = [client.get(target.site_url + path, headers={'Cache-Control': 'no-cache'},
            params={'undrift_commit': merge_sha}) for path in ('/build.json', '/content/returns.txt', '/returns/')]
        if any(r.status_code != 200 or len(r.content) > 500_000 for r in responses):
            raise workflow.Conflict('Production site is unavailable or requires authentication')
        build = responses[0].json()
        if not isinstance(build, dict):
            raise workflow.Conflict('Production build metadata is invalid')
        if build.get('commit') != merge_sha:
            raise workflow.Conflict('Production site is still serving a different commit')
        if responses[1].content != plan['proposed_content'].encode('utf-8'):
            raise workflow.Conflict('Production policy bytes do not match the approved correction')
        page = PolicyText()
        try:
            page.feed(responses[2].text)
        except (AssertionError, ValueError):
            raise workflow.Conflict('Production Returns page is invalid') from None
        if page.matches != 1 or ''.join(page.parts) != plan['proposed_content']:
            raise workflow.Conflict('Rendered Returns page does not match the approved correction')
        checked_at = now()
        content_sha256 = hashlib.sha256(responses[1].content).hexdigest()
        return {'verified_at': checked_at, 'site_url': target.site_url,
                'content_sha256': content_sha256,
                'verification_evidence': {
                    'source': 'production_site', 'site_url': target.site_url, 'checked_at': checked_at,
                    'expected_merge_commit': merge_sha, 'observed_build_commit': build['commit'],
                    'approved_content_sha256': hashlib.sha256(plan['proposed_content'].encode('utf-8')).hexdigest(),
                    'raw_policy_sha256': content_sha256,
                    'rendered_policy_sha256': hashlib.sha256(''.join(page.parts).encode('utf-8')).hexdigest(),
                    'returns_page_sha256': hashlib.sha256(responses[2].content).hexdigest(),
                    'policy_element_count': page.matches,
                    'http_statuses': dict(zip(('/build.json', '/content/returns.txt', '/returns/'),
                                              (response.status_code for response in responses))),
                }}
    except (httpx.HTTPError, ValueError):
        raise workflow.WorkflowError('Production verification request failed') from None
    finally:
        if owned:
            client.close()


class Publisher:
    def __init__(self, store: Store, target: Target, journal=None, github=None, web=None):
        self.store, self.target = store, target
        self.path = Path(journal or config.ROOT / 'data' / 'publishing.db')
        if self.path.resolve() == store.path.resolve() and store.metadata('unified_publications') != '1':
            raise workflow.WorkflowError('Publishing journal must be separate from the application database')
        self.github = github or GitHub(target)
        self.web = web
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.write() as c:
            for statement in JOURNAL_SCHEMA:
                c.execute(statement)

    @contextmanager
    def write(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except sqlite3.OperationalError:
            c.rollback()
            raise workflow.Conflict('Publishing journal is busy; retry the same request') from None
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def key(self, proposal_id):
        return self.store.identity(), proposal_id

    def event(self, c, proposal_id, event, **metadata):
        c.execute('INSERT INTO publication_events (ts,database_id,proposal_id,type,metadata) VALUES (?,?,?,?,?)',
            (now(), *self.key(proposal_id), 'publishing.' + event, json.dumps(config.safe_metadata(metadata))))

    def get(self, proposal_id):
        with self.write() as c:
            row = c.execute('SELECT plan,state FROM publications WHERE database_id=? AND proposal_id=?', self.key(proposal_id)).fetchone()
            if not row:
                return None
            state = json.loads(row['state'])
            plan = json.loads(row['plan'])
            events = [dict(r) | {'metadata': json.loads(r['metadata'])} for r in c.execute(
                'SELECT * FROM publication_events WHERE database_id=? AND proposal_id=? ORDER BY seq', self.key(proposal_id))]
            return {**state, 'digest': plan['digest'], 'target': plan['target'], 'events': events}

    def approved(self, proposal_id):
        with self.store.read() as c:
            p = self.store.proposal(proposal_id, c)
            if not p:
                raise workflow.NotFound('Proposal not found')
            if p.status != 'approved' or not p.reviewed_by:
                raise workflow.Conflict('Publishing requires a human-approved proposal')
            doc = self.store.document(p.document_id, conn=c)
            policy = self.store.policy(p.policy_id, conn=c)
            if (p.document_id != self.target.document_id or not doc or not policy
                    or doc.version != p.base_version or doc.content != p.original_content
                    or policy.version != p.policy_version):
                raise workflow.Conflict('Approved proposal or publishing target is stale')
            return p

    def plan(self, proposal_id, token):
        workflow.authorize(self.store, token, 'publishing.preview', ('proposal', proposal_id))
        p = self.approved(proposal_id)
        with self.write() as c:
            existing = c.execute('SELECT plan FROM publications WHERE database_id=? AND proposal_id=?', self.key(proposal_id)).fetchone()
        if existing:
            plan = json.loads(existing['plan'])
            self.check_binding(p, plan)
            return plan
        snapshot = self.github.snapshot()
        if snapshot['content'] != p.original_content:
            raise workflow.Conflict('Repository file differs from the reviewed original; reanalyse before publishing')
        plan = {'database_id': self.store.identity(), 'proposal_id': p.id, 'target': asdict(self.target),
            'base_commit': snapshot['commit'], 'base_tree': snapshot['tree'], 'base_blob': snapshot['blob'],
            'base_version': p.base_version, 'policy_version': p.policy_version,
            'original_content': p.original_content, 'proposed_content': p.proposed_content,
            'reviewed_by': p.reviewed_by, 'reviewed_at': p.reviewed_at.isoformat()}
        plan['digest'] = digest(plan)
        plan['head_branch'] = f"undrift/{plan['database_id'][:8]}/{p.id}-{plan['digest'][:10]}"
        return plan

    def check_binding(self, p, plan):
        if (plan['target'] != asdict(self.target) or plan['original_content'] != p.original_content
                or plan['proposed_content'] != p.proposed_content or plan['policy_version'] != p.policy_version
                or plan['base_version'] != p.base_version or plan['reviewed_by'] != p.reviewed_by
                or plan['reviewed_at'] != p.reviewed_at.isoformat()):
            raise workflow.Conflict('Publication authorization no longer matches the approved proposal or target')

    def publish(self, proposal_id, approval_digest, token):
        actor = workflow.authorize(self.store, token, 'publishing.create_pr', ('proposal', proposal_id))
        plan = self.plan(proposal_id, token)
        if approval_digest != plan['digest']:
            raise workflow.Conflict('Repository plan changed; review and authorize the exact change again')
        with self.write() as c:
            row = c.execute('SELECT plan FROM publications WHERE database_id=? AND proposal_id=?', self.key(proposal_id)).fetchone()
            if row and json.loads(row['plan']) != plan:
                raise workflow.Conflict('A different publication is already authorized')
            if not row:
                c.execute('INSERT INTO publications VALUES (?,?,?,?)', (*self.key(proposal_id), json.dumps(plan),
                    json.dumps({'status': 'approved', 'authorized_by': actor, 'authorized_at': now()})))
                self.event(c, proposal_id, 'approved', actor=actor, digest=plan['digest'], repository=self.target.repository)
        try:
            with self.write() as c:
                row = c.execute('SELECT state FROM publications WHERE database_id=? AND proposal_id=?', self.key(proposal_id)).fetchone()
                state = json.loads(row['state'])
                self.check_binding(self.approved(proposal_id), plan)
                if not state.get('pr'):
                    state.update(pr=self.github.ensure_pr(plan), status='pr_created', error=None)
                    c.execute('UPDATE publications SET state=? WHERE database_id=? AND proposal_id=?', (json.dumps(state), *self.key(proposal_id)))
                    self.event(c, proposal_id, 'pr_created', actor=actor, pr_number=state['pr']['number'], commit=state['pr']['commit'])
                else:
                    remote = self.github.request('GET', f"pulls/{state['pr']['number']}")
                    if not remote:
                        raise workflow.Conflict('Publishing PR is no longer accessible')
                    self.github.check_pr(remote, plan, state['pr']['commit'])
        except workflow.WorkflowError as exc:
            self.failure(proposal_id, exc)
            raise
        return self.get(proposal_id)

    def failure(self, proposal_id, exc):
        with self.write() as c:
            row = c.execute('SELECT state FROM publications WHERE database_id=? AND proposal_id=?', self.key(proposal_id)).fetchone()
            if row:
                state = json.loads(row['state'])
                if state['status'] != 'blocked':
                    state['last_success_status'] = state['status']
                state['status'] = 'blocked'
                state['error'] = config.safe_text(str(exc))
                for key in VERIFICATION_FIELDS:
                    state.pop(key, None)
                c.execute('UPDATE publications SET state=? WHERE database_id=? AND proposal_id=?', (json.dumps(state), *self.key(proposal_id)))
            self.event(c, proposal_id, 'failed', code=exc.code, message=str(exc))

    def refresh(self, proposal_id, token):
        actor = workflow.authorize(self.store, token, 'publishing.refresh', ('proposal', proposal_id))
        try:
            with self.write() as c:
                row = c.execute('SELECT plan,state FROM publications WHERE database_id=? AND proposal_id=?', self.key(proposal_id)).fetchone()
                if not row:
                    raise workflow.NotFound('No publication exists')
                plan, state = json.loads(row['plan']), json.loads(row['state'])
                if plan['target'] != asdict(self.target):
                    raise workflow.Conflict('Publishing target configuration changed')
                if not state.get('pr'):
                    raise workflow.Conflict('Create the reviewed PR before checking deployment')
                previous = state['status']
                progress = self.github.progress(plan, state['pr'])
                for key in (*VERIFICATION_FIELDS, 'deployment_id', 'github_deployment'):
                    state.pop(key, None)
                state.update(progress, error=None, checked_at=now())
                verification_error = None
                if state['status'] == 'merged':
                    try:
                        deployment = state.get('github_deployment')
                        if deployment and deployment['state'] != 'success':
                            raise workflow.Conflict('GitHub production deployment has not succeeded',
                                                    deployment_state=deployment['state'])
                        state.update(verify_site(self.target, plan, state['merge_commit'], self.web), status='verified')
                    except workflow.WorkflowError as exc:
                        verification_error = str(exc)
                        state['error'] = verification_error
                c.execute('UPDATE publications SET state=? WHERE database_id=? AND proposal_id=?', (json.dumps(state), *self.key(proposal_id)))
                if previous != state['status']:
                    # Keep each independently observed milestone in the audit trail.
                    stages = ['approved', 'pr_created', 'merged', 'deployed', 'verified']
                    before = stages.index(previous) if previous in stages else 0
                    for stage in stages[before+1:stages.index(state['status'])+1]:
                        proof = {'verification_evidence': state['verification_evidence']} if stage in {'deployed', 'verified'} else {}
                        self.event(c, proposal_id, stage, actor=actor, merge_commit=state.get('merge_commit'), **proof)
                self.event(c, proposal_id, 'checked', actor=actor, status=state['status'], error=verification_error,
                           verification_evidence=state.get('verification_evidence'),
                           github_deployment=state.get('github_deployment'))
        except workflow.WorkflowError as exc:
            self.failure(proposal_id, exc)
            raise
        return self.get(proposal_id)


def configured(store):
    target = Target.configured()
    journal = os.environ.get('UNDRIFT_PUBLISH_DB') or (store.path if store.metadata('unified_publications') == '1' else None)
    return Publisher(store, target, journal) if target else None
