"""Run-scoped lexical discovery. Search snippets never count as authenticated retrieval."""
from collections import Counter
import hashlib
import json
import math
import re

import config
from store import Store
import workflow

WORD = re.compile(r'\w+', re.UNICODE)
STOP = frozenset('a an and are as at be by can for from in is it of on or our the this to within with your'.split())


def tokens(text):
    return [w for w in WORD.findall(text.casefold()) if w not in STOP]


def _scope(store, c, run_id, run_token):
    run = workflow.authorize_run(store, run_id, run_token, c)
    docs = [store.document(did, conn=c) for did in run.document_ids]
    if any(d is None or d.version != run.document_versions.get(d.id) for d in docs):
        raise workflow.Conflict('document versions changed during the run')
    return run, docs


def _event(store, c, run, actor, operation, results, **extra):
    store.add_event(c, 'agent.discovery', actor=actor, subject=('run', run.id), metadata={
        'run_id': run.id, 'operation': operation, 'result_documents': results, **extra})


def list_channels(store: Store, *, run_id, run_token, actor='mcp:discovery'):
    store.reap_runs()
    with store.write() as c:
        run, docs = _scope(store, c, run_id, run_token)
        counts = Counter(d.channel for d in docs)
        channels = [dict(r) | {'document_count': counts[r['id']]} for r in c.execute(
            'SELECT * FROM channels ORDER BY id') if r['id'] in counts]
        _event(store, c, run, actor, 'list_channels', [], document_count=len(docs))
        return {'run_id': run.id, 'channels': channels, 'document_count': len(docs)}


def _limit(limit, offset=0):
    if type(limit) is not int or not 1 <= limit <= 25 or type(offset) is not int or not 0 <= offset <= 10000:
        raise workflow.WorkflowError('limit must be 1-25 and offset must be 0-10000')


def search_documents(store: Store, *, run_id, run_token, query='', channel_id=None,
                     limit=10, offset=0, actor='mcp:discovery'):
    _limit(limit, offset)
    if not isinstance(query, str) or len(query) > 500 or config.redact(query, run_token) != query:
        raise workflow.WorkflowError('search query must be at most 500 characters and contain no credentials')
    terms = WORD.findall(query.casefold())
    if query.strip() and not terms:
        raise workflow.WorkflowError('search query must contain a word or number')
    if len(terms) > 32:
        raise workflow.WorkflowError('search query contains too many terms')
    store.reap_runs()
    with store.write() as c:
        run, docs = _scope(store, c, run_id, run_token)
        if channel_id is not None and channel_id not in {d.channel for d in docs}:
            raise workflow.WorkflowError('channel is outside the run scope')
        allowed = [d.id for d in docs if channel_id is None or d.channel == channel_id]
        placeholders = ','.join('?' for _ in allowed)
        clause = f'document_id IN ({placeholders})'
        params = allowed
        if terms:
            # Literal words joined with OR: no user-controlled FTS operators or SQL.
            clause += ' AND document_search MATCH ?'
            params = [*params, ' OR '.join('"' + w + '"' for w in terms)]
        total = c.execute('SELECT COUNT(*) FROM document_search WHERE ' + clause, params).fetchone()[0]
        rank = 'bm25(document_search)' if terms else '0.0'
        rows = c.execute(f'SELECT document_id, channel_id, version, title, content, {rank} AS rank '
                         'FROM document_search WHERE ' + clause + ' ORDER BY rank, document_id LIMIT ? OFFSET ?',
                         [*params, limit, offset]).fetchall()
        results = [{'document_id': r['document_id'], 'channel': r['channel_id'], 'version': int(r['version']),
                    'title': r['title'], 'snippet': r['content'][:320], 'score': -float(r['rank'])} for r in rows]
        _event(store, c, run, actor, 'search_documents', [r['document_id'] for r in results],
               query_sha256=hashlib.sha256(query.encode()).hexdigest(), channel_id=channel_id,
               query=query[:300], query_truncated=len(query) > 300,
               catalog_browse=not terms,
               termset_sha256=hashlib.sha256(' '.join(sorted(set(terms))).encode()).hexdigest(),
               has_words=any(w.isalpha() and w not in STOP for w in terms),
               number_free=not any(w.isdigit() for w in terms),
               limit=limit, offset=offset)
        return {'run_id': run.id, 'total': total, 'offset': offset, 'results': results, 'retrieval_required': True}


def similar_documents(store: Store, *, run_id, run_token, document_id, limit=5, actor='mcp:discovery'):
    _limit(limit)
    store.reap_runs()
    with store.write() as c:
        run, docs = _scope(store, c, run_id, run_token)
        source = next((d for d in docs if d.id == document_id), None)
        if source is None:
            raise workflow.WorkflowError('source document is outside the run scope')
        words = {d.id: Counter(tokens(d.title + ' ' + d.content)) for d in docs}
        frequency = Counter(w for terms in words.values() for w in terms)
        vectors = {did: {w: (1 + math.log(n)) * (math.log((1 + len(docs)) / (1 + frequency[w])) + 1)
                         for w, n in terms.items()} for did, terms in words.items()}
        norms = {did: math.sqrt(sum(x * x for x in v.values())) for did, v in vectors.items()}
        reference = vectors[source.id]
        results = []
        for d in docs:
            if d.id == source.id:
                continue
            denominator = norms[source.id] * norms[d.id]
            score = sum(value * vectors[d.id].get(w, 0) for w, value in reference.items()) / denominator if denominator else 0.0
            if score > 0:
                results.append({'document_id': d.id, 'title': d.title, 'channel': d.channel,
                                'version': d.version, 'score': round(score, 10)})
        results = sorted(results, key=lambda r: (-r['score'], r['document_id']))[:limit]
        _event(store, c, run, actor, 'similar_documents', [r['document_id'] for r in results], source_document=document_id)
        return {'run_id': run.id, 'method': 'tfidf_cosine', 'results': results, 'retrieval_required': True}


def validate_search_strategy(store, run, conn):
    """Verify observable search-first behavior for newly versioned discovery runs.

    This checks audit evidence, not the agent's self-report or the relevance of its
    reasoning. It cannot establish semantic diversity or enforce a monetary cap.
    Old runs are never regraded or assigned this new contract.
    """
    if run.metadata.get('discovery_strategy') != 'search_first_v1':
        return {'enforced': False}
    events = []
    for row in conn.execute("SELECT seq,type,subject_id,status,metadata FROM events "
                            "WHERE type IN ('agent.discovery','agent.retrieval','mcp.tool_call') ORDER BY seq"):
        meta = json.loads(row['metadata'])
        if meta.get('run_id') == run.id:
            events.append((row, meta))
    searches = [(row, meta) for row, meta in events if row['type'] == 'agent.discovery'
                and meta.get('operation') == 'search_documents']
    catalog = [(row, meta) for row, meta in searches if meta.get('catalog_browse')]
    first_catalog = catalog[0][0]['seq'] if catalog else float('inf')
    formulated = [(row, meta) for row, meta in searches
                  if row['seq'] < first_catalog and meta.get('has_words')]
    distinct = {meta.get('termset_sha256') for _, meta in formulated}
    distinct.discard(None)
    catalog_ids = {did for _, meta in catalog for did in meta.get('result_documents', [])}
    lead_followed = any(row['type'] == 'agent.retrieval' and row['status'] == 'ok'
        and meta.get('kind') == 'document' and row['seq'] < first_catalog
        and any(query_row['seq'] < row['seq'] and row['subject_id'] in query.get('result_documents', [])
                for query_row, query in formulated) for row, meta in events)
    no_leads = not any(meta.get('result_documents') for _, meta in formulated)
    counts = Counter(meta.get('tool') for row, meta in events if row['type'] == 'mcp.tool_call')
    counts['search_documents'] = max(counts['search_documents'], len(searches))
    counts['similar_documents'] = max(counts['similar_documents'], sum(
        row['type'] == 'agent.discovery' and meta.get('operation') == 'similar_documents' for row, meta in events))
    observed = {'enforced': True, 'distinct_formulated_queries_before_catalog': len(distinct),
        'number_free_query': any(meta.get('number_free') for _, meta in formulated),
        'lead_followed': lead_followed, 'no_search_leads': no_leads,
        'catalog_complete': catalog_ids == set(run.document_ids),
        'search_calls': counts['search_documents'], 'similarity_calls': counts['similar_documents']}
    if (len(distinct) < 3 or not observed['number_free_query'] or not observed['catalog_complete']
            or not (lead_followed or no_leads) or counts['search_documents'] > 12 or counts['similar_documents'] > 4):
        raise workflow.WorkflowError('discovery search-first contract was not satisfied', observed=observed)
    return observed
