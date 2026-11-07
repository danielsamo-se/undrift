"""Server-owned channel delegation; no network, MCP tools or agent actor bypass.

Settings delegate only future runs. An auto-application is a bounded local document
version, never a human approval, remote publication, GitHub merge or deployment.
"""
import difflib

import config
from store import now
import workflow as w

AUTO_CHANNELS = frozenset({'help-center', 'support-snippets'})
WEBSITE_CHANNELS = frozenset({'website', 'website-faq', 'website-shipping'})
ACTOR = 'autonomy:bounded-local'
GLOBAL_RUN_LIMIT = 3


def installed(c):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='channel_autonomy'").fetchone())


def policy(store, channel_id, c):
    if channel_id in w.MANUAL_ONLY_CHANNELS:
        allowed, default = ['manual_only'], 'manual_only'
    elif channel_id in WEBSITE_CHANNELS:
        allowed, default = ['pull_request'], 'pull_request'
    elif channel_id in AUTO_CHANNELS:
        allowed, default = ['review_required', 'bounded_auto'], 'review_required'
    else:
        allowed, default = ['review_required'], 'review_required'
    row = c.execute('SELECT * FROM channel_autonomy WHERE channel_id=?', (channel_id,)).fetchone() if installed(c) else None
    result = dict(row) if row else {'channel_id':channel_id,'version':0,'mode':default,'max_per_run':1,
                                     'updated_at':None,'updated_by':None,'note':''}
    # Hard channel ceilings remain authoritative even if a stored mode is corrupt.
    result['configuration_valid'] = result['mode'] in allowed
    result['allowed_modes'] = allowed
    result['effective_mode'] = result['mode'] if result['configuration_valid'] else default
    result['persisted'] = row is not None
    result['schema_available'] = installed(c)
    return result


def listing(store, conn=None):
    with store.read(conn) as c:
        return [dict(ch) | policy(store,ch['id'],c) for ch in store.channels(c)]


def configure(store, channel_id, *, token, expected_version, mode, max_per_run=1, note=''):
    actor = w.authorize(store,token,'autonomy.configure',('channel',channel_id))
    try:
        if type(expected_version) is not int or expected_version<0 or type(max_per_run) is not int or not 1<=max_per_run<=3:
            raise w.WorkflowError('invalid autonomy version or bound')
        if not isinstance(note,str) or not note.strip() or len(note)>1000 or config.redact(note)!=note:
            raise w.WorkflowError('a non-secret delegation note is required (1-1000 characters)')
        with store.write() as c:
            if channel_id not in {ch['id'] for ch in store.channels(c)}:
                raise w.NotFound('unknown channel')
            if not installed(c):
                raise w.Conflict('Autonomy needs an explicitly migrated operational database')
            old=policy(store,channel_id,c)
            if mode not in old['allowed_modes']:
                raise w.GuardrailViolation('channel autonomy ceiling forbids this mode',channel_id=channel_id)
            if expected_version!=old['version']:
                raise w.Conflict('channel autonomy changed; reload before saving',current_version=old['version'])
            c.execute('INSERT INTO channel_autonomy VALUES (?,?,?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET '
                      'version=excluded.version,mode=excluded.mode,max_per_run=excluded.max_per_run,'
                      'updated_at=excluded.updated_at,updated_by=excluded.updated_by,note=excluded.note',
                      (channel_id,expected_version+1,mode,max_per_run,now(),actor,note.strip()))
            store.add_event(c,'autonomy.policy_changed',actor='reviewer:'+actor,subject=('channel',channel_id),
                metadata={'channel_id':channel_id,'from_version':expected_version,'version':expected_version+1,
                          'from_mode':old['effective_mode'],'mode':mode,'max_per_run':max_per_run,'note':note.strip(),
                          'future_runs_only':True})
            return policy(store,channel_id,c)
    except w.WorkflowError as exc:
        store.record_event('autonomy.configuration_denied',actor='reviewer:'+actor,status='denied',
            subject=('channel',channel_id),metadata={'code':exc.code},error=str(exc))
        raise


def pin_run(store, run_id, docs, c):
    if not installed(c):return
    for doc in docs:
        p=policy(store,doc.channel,c)
        c.execute('INSERT INTO run_autonomy_grants VALUES (?,?,?,?,?,?)',
                  (run_id,doc.id,doc.channel,p['version'],p['effective_mode'],p['max_per_run']))


def receipt(store, proposal_id, c):
    if not installed(c):return None
    row=c.execute('SELECT * FROM autonomy_applications WHERE proposal_id=?',(proposal_id,)).fetchone()
    return dict(row) if row else None


def decision(store, proposal, c):
    doc=store.document(proposal.document_id,conn=c)
    if doc is None:return {'mode':'manual_only','eligible':False,'blockers':['document_missing'],'receipt':None,'can_apply_locally':False}
    p=policy(store,doc.channel,c);blockers=[]
    mode=p['effective_mode']
    if mode!='bounded_auto':blockers.append(mode)
    if not p['configuration_valid']:blockers.append('invalid_channel_configuration')
    if proposal.status!='pending_review':blockers.append('proposal_not_pending')
    grant=c.execute('SELECT * FROM run_autonomy_grants WHERE run_id=? AND document_id=?',
                    (proposal.run_id,doc.id)).fetchone() if installed(c) else None
    if not grant or (grant['channel_id'],grant['policy_version'],grant['mode'],grant['max_per_run'])!=(
            doc.channel,p['version'],mode,p['max_per_run']) or p['version']==0:
        blockers.append('delegation_not_pinned_or_changed')
    run=store.run(proposal.run_id,c) if proposal.run_id else None
    finding=next((f for f in store.findings(run_id=proposal.run_id,conn=c) if f.id==proposal.finding_id),None) if run else None
    if not run or run.status!='completed' or run.metadata.get('reconciliation')!='verified_reply_v1':
        blockers.append('run_not_fully_reconciled')
    elif any(f.category=='UNKNOWN' for f in store.findings(run_id=run.id,conn=c)):
        blockers.append('unknown_findings')
    if not finding or finding.category!='OUTDATED' or not run or not w.verified_finding(run,finding) or finding.source=='legacy':
        blockers.append('no_verified_outdated_finding')
    elif (finding.document_id,finding.document_version,finding.policy_id,finding.policy_version,finding.evidence)!=(
            doc.id,proposal.base_version,proposal.policy_id,proposal.policy_version,proposal.evidence) or (
            finding.proposed_content is not None and finding.proposed_content!=proposal.proposed_content):
        blockers.append('finding_binding_mismatch')
    if doc.version!=proposal.base_version or doc.content!=proposal.original_content:
        blockers.append('document_changed')
    current,stale=w._policy_context(store,proposal.policy_id,c)
    if current.version!=proposal.policy_version:blockers.append('policy_changed')
    violations,flags=w.check_guardrails(proposal.original_content,proposal.proposed_content,
        current_days=current.voluntary_return_days,stale=stale,evidence=proposal.evidence,effective_from=current.effective_from)
    if violations:blockers.append('content_guardrail_violation')
    if flags:blockers.append('semantic_edit_needs_review')
    old=w.split_sentences(proposal.original_content);new=w.split_sentences(proposal.proposed_content)
    matcher=difflib.SequenceMatcher(a=old,b=new,autojunk=False)
    changes=[(tag,i,j,k,l) for tag,i,j,k,l in matcher.get_opcodes() if tag!='equal']
    if len(changes)!=1 or changes[0][0]!='replace' or changes[0][2]-changes[0][1]!=1 or changes[0][4]-changes[0][3]!=1:
        blockers.append('not_single_sentence_correction')
    if any(w.protection(s) for s in old+new):blockers.append('policy_sensitive_exception')
    if not proposal.evidence or proposal.evidence not in proposal.original_content:blockers.append('invalid_evidence')
    if run:
        retrieval=w.retrieval_status(store,run,c)
        if not retrieval['complete']:blockers.append('unverified_retrieval')
        from clarification import phase
        ph=phase(store,run.id,c)
        if ph and c.execute('SELECT 1 FROM clarification_questions q JOIN conversation_phases p ON p.run_id=q.run_id '
                           'WHERE p.conversation_id=? AND q.answer IS NULL',(ph['conversation_id'],)).fetchone():
            blockers.append('unresolved_clarification')
        for other in store.runs(c):
            if other.id!=run.id and other.status=='open' and doc.id in other.document_ids:
                blockers.append('concurrent_analysis');break
        for f in store.findings(conn=c):
            if f.run_id!=run.id and f.document_id==doc.id and f.policy_version==proposal.policy_version and f.document_version==proposal.base_version:
                other=store.run(f.run_id,c)
                if w.verified_finding(other,f) and (f.category!='OUTDATED' or (f.proposed_content is not None and f.proposed_content!=proposal.proposed_content)):
                    blockers.append('conflicting_verified_evidence');break
    if installed(c) and run:
        total=c.execute('SELECT COUNT(*) FROM autonomy_applications WHERE run_id=?',(run.id,)).fetchone()[0]
        channel=c.execute('SELECT COUNT(*) FROM autonomy_applications WHERE run_id=? AND channel_id=?',(run.id,doc.channel)).fetchone()[0]
        if total>=GLOBAL_RUN_LIMIT or channel>=p['max_per_run']:blockers.append('run_application_limit')
    return {'mode':mode,'policy_version':p['version'],'channel_id':doc.channel,'eligible':not blockers,
            'blockers':list(dict.fromkeys(blockers)),'receipt':receipt(store,proposal.id,c),
            'can_apply_locally':mode not in {'manual_only','pull_request'},'future_runs_only':True}


def apply_pending(store, run_id, c):
    """Trusted finalizer only; never exposed as an MCP tool or unauthenticated API."""
    applied=[]
    if not installed(c):return applied
    for proposal in store.proposals(conn=c):
        if proposal.run_id!=run_id or proposal.status!='pending_review':continue
        d=decision(store,proposal,c)
        if not d['eligible']:
            store.add_event(c,'autonomy.application_denied',actor=ACTOR,status='denied',subject=('proposal',proposal.id),
                metadata={'run_id':run_id,'proposal_id':proposal.id,'channel_id':d.get('channel_id'),
                          'policy_version':d.get('policy_version'),'blockers':d['blockers']})
            continue
        version=proposal.base_version+1;ts=now()
        c.execute('INSERT INTO document_versions VALUES (?,?,?,?,?)',
                  (proposal.document_id,version,proposal.proposed_content,ts,ACTOR+' via '+proposal.id))
        if c.execute('UPDATE documents SET current_version=? WHERE id=? AND current_version=?',
                     (version,proposal.document_id,proposal.base_version)).rowcount!=1:
            raise w.Conflict('document changed during autonomous application')
        if c.execute("UPDATE proposals SET status='applied',applied_version=?,applied_at=? WHERE id=? AND status='pending_review'",
                     (version,ts,proposal.id)).rowcount!=1:
            raise w.Conflict('proposal changed during autonomous application')
        c.execute('INSERT INTO autonomy_applications VALUES (?,?,?,?,?,?,?,?,?)',
                  (proposal.id,run_id,proposal.document_id,d['channel_id'],d['policy_version'],
                   w._sha(proposal.original_content),w._sha(proposal.proposed_content),version,ts))
        meta={'run_id':run_id,'proposal_id':proposal.id,'document_id':proposal.document_id,'channel_id':d['channel_id'],
              'autonomy_version':d['policy_version'],'base_version':proposal.base_version,'version':version,
              'original_sha256':w._sha(proposal.original_content),'proposed_sha256':w._sha(proposal.proposed_content),
              'authorization_origin':'channel_delegation','human_patch_approval':False}
        store.add_event(c,'document.version_created',actor=ACTOR,subject=('document',proposal.document_id),metadata=meta)
        store.add_event(c,'proposal.auto_applied',actor=ACTOR,subject=('proposal',proposal.id),metadata=meta)
        applied.append({'proposal_id':proposal.id,'document_id':proposal.document_id,'applied_version':version})
    return applied
