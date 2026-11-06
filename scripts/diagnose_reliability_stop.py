"""Read-only stop diagnosis; never edits/regrades the frozen evaluation result."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import discovery
from store import Store
import workflow

def main():
    report_path=ROOT/'evaluations/reliability-v1-results.json'
    original=report_path.read_bytes(); report=json.loads(original)
    assert report['state']=='stopped' and len(report['results'])==1
    result=report['results'][0]
    store=Store(ROOT/result['database'])
    provider_path=ROOT/'.tools/reliability-v1/provider-1-1.json'
    provider=json.loads(provider_path.read_text(encoding='utf-8'))
    with store.snapshot() as c:
        run=store.run(result['root_run_id'],c)
        try:
            observed=discovery.validate_search_strategy(store,run,c); failure=None
        except workflow.WorkflowError as exc:
            failure=str(exc); observed=exc.details.get('observed')
        qs=c.execute('SELECT COUNT(*) FROM clarification_questions WHERE run_id=?',(run.id,)).fetchone()[0]
        all_events=store.events(limit=100000)
        relevant=[e for e in all_events if e.metadata.get('run_id')==run.id]
        catalog=next(e.seq for e in relevant if e.type=='agent.discovery' and e.metadata.get('catalog_browse'))
        first_doc=next(e.seq for e in relevant if e.type=='agent.retrieval' and e.metadata.get('kind')=='document')
        truth=json.loads((ROOT/'tests/data/discovery_ground_truth.json').read_text(encoding='utf-8'))
        expected={did for did,category in truth.items() if category=='UNKNOWN'}
        import re
        emitted={re.match(r'\[document:([^;]+);version:',q['prompt'])[1] for q in provider['questions']}
        diagnosis={'version':'reliability-v1','original_report_sha256':hashlib.sha256(original).hexdigest(),
            'canonical_report_sha256':hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
            'summary':'Stopped after evaluation 1: search leads were retrieved only after catalog browsing. Native question capture rejected the search-order violation; no continuation or later conversation was started.',
            'run_id':run.id,'provider_status':provider['status'],'provider_native_question_count':len(provider['questions']),
            'persisted_question_count':qs,'provider_question_document_ids':sorted(emitted),
            'provider_native_emission_recall':len(expected & emitted)/len(expected),
            'provider_native_emission_precision':len(expected & emitted)/len(emitted),
            'failure':failure,'failed_rule':'lead_retrieval_before_catalog','search_validation':observed,
            'catalog_event_seq':catalog,'first_document_retrieval_seq':first_doc,
            'provider_snapshot_sha256':hashlib.sha256(provider_path.read_bytes()).hexdigest(),
            'grading_note':'Provider emission is separate diagnostic evidence. The original frozen product-persistence and reconciliation scores are unchanged.',
            'remaining_conversations_not_started':4,'paid_messages_attempted':1,
            'partial_observation_metrics':result['observed_unreconciled_metrics']}
    assert original==report_path.read_bytes()
    target=ROOT/'evaluations/reliability-v1-stop-diagnosis.json'
    target.write_text(json.dumps(diagnosis,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(diagnosis,indent=2))

if __name__=='__main__': main()
