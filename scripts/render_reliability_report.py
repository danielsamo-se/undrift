"""Render the preserved JSON evaluation result as Markdown; no providers or DB writes."""
import json
import hashlib
import argparse
from pathlib import Path
import re
from statistics import mean
import sys

ROOT=Path(__file__).resolve().parents[1]

def render(report):
    series=report.get('series_id','reliability-v1')
    if not re.fullmatch(r'reliability-v[1-9][0-9]*',series):
        raise ValueError('Invalid evaluation series')
    a=report['aggregate']
    def number(value,percent=True):
        return 'N/A' if value is None else f'{value*100:.2f}%' if percent else f'{value:.2f}'
    lines=[f'# Undrift Reliability Evaluation {series.removeprefix("reliability-")}','',
        f"State: **{report['state']}**. Recorded conversations: **{a['recorded_conversations']}/{a['planned_conversations']}**. "
        f"Reconciled: **{a['reconciled_conversations']}**. Attempted paid messages: **{report.get('attempted_paid_messages',a['message_count'])}/10**.",'',
        f"Frozen protocol SHA-256: `{report.get('protocol_sha256','unfrozen')}`.",'',
        'Five independently seeded stores use the same frozen instructions, model, tool configuration and rubric. '
        'Native question answers come from the precommitted private unavailable-facts fixture. '
        'The earlier development runs are excluded. No unfavorable result is replaced.','',
        '| Metric | Minimum | Mean | Maximum | Defined / recorded |',
        '| --- | ---: | ---: | ---: | ---: |']
    for key,s in a['statistics'].items():
        percentage=key not in {'false_positives','outdated_misses','duration_s','reserved_parent_steps','observed_llm_rounds','native_question_count','mcp_error_count'}
        lines.append(f"| {key} | {number(s['minimum'],percentage)} | {number(s['mean'],percentage)} | {number(s['maximum'],percentage)} | {s['defined_count']}/{s['result_count']} |")
    lines+=['','| Conversation | Root run | Final state | Reconciled | Accuracy | Outdated recall / precision | UNKNOWN recall | Native clarification recall | Messages | Reported cost | Duration |',
        '| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in report['results']:
        m=r['metrics']
        lines.append(f"| {r['slot']} | `{r['root_run_id']}` | {r['status']} | {r['reconciled']} | {number(m['accuracy'])} | {number(m['outdated_recall'])} / {number(m['outdated_precision'])} | {number(m['unknown_recall'])} | {number(m['native_clarification_recall'])} | {r['message_count']} | {r['reported_cost'] or 'unavailable'} | {r['duration_s']:.1f}s |")
    lines+=['',f"Cumulative reported evaluation cost: **{report.get('cumulative_reported_cost',a['reported_total_cost'])}** platform units. "
        'The API does not identify a currency. The stop threshold is checked between messages, not within a message.','',
        f"Not started: **{a['not_started']}**. Historical preservation passed: **{report.get('protected_historical_files_unchanged','not checked')}**."]
    records=report['results']
    lines+=['',f"Genuine persisted native questions: **{sum(r['native_question_count'] for r in records)}**; "
        f"fixture answers: **{sum(r['answered_count'] for r in records)}**. "
        f"Protected-channel automation safety: **{sum(r['protected_automation_safe'] for r in records)}/{len(records)}**.",
        f"Local pending proposals: **{sum(r['proposal_count'] for r in records)}**. No proposal approval or publication is part of this study."]
    if report.get('stop_reason'): lines+=['',f"Stop reason: **{report['stop_reason']}**. No automatic retry was started."]
    costs=a.get('cost_statistics',{})
    if costs:
        lines+=['',f"Per-conversation cost: minimum **{costs['minimum']}**, mean **{costs['mean']}**, maximum **{costs['maximum']}** platform units."]
    lines+=['','## Discovery and clarification evidence','',
        '| Conversation | Questions / answered | Search / similarity calls | Lead before catalog | MCP errors | Pending proposals |',
        '| --- | ---: | ---: | --- | ---: | ---: |']
    for r in report['results']:
        strategy=r.get('search_strategy') or {}
        lines.append(f"| {r['slot']} | {r['native_question_count']} / {r['answered_count']} | {r['mcp_calls'].get('search_documents',0)} / {r['mcp_calls'].get('similar_documents',0)} | {strategy.get('lead_followed','not recorded')} | {r['mcp_error_count']} | {r['proposal_count']} |")
    if report['results']:
        lines+=['','| Tool | Minimum calls | Mean calls | Maximum calls | Total calls |',
                '| --- | ---: | ---: | ---: | ---: |']
        for tool in sorted({tool for r in report['results'] for tool in r['mcp_calls']}):
            values=[r['mcp_calls'].get(tool,0) for r in report['results']]
            lines.append(f'| {tool} | {min(values)} | {mean(values):.2f} | {max(values)} | {sum(values)} |')
    for r in report['results']:
        lines+=['',f"### Conversation {r['slot']}: `{r['root_run_id']}`",'',
            f"Thread: `{r['thread_id']}`. Database: `{r['database']}` (identity `{r['database_id']}`).",
            f"Phase run IDs: {', '.join('`'+v+'`' for v in r['phase_run_ids'])}.",
            f"Assistant message IDs: {', '.join('`'+v+'`' for v in r['message_ids'])}.",'',
            f"Native evidence integrity: **{r['native_question_integrity']}**. Protected-channel automation safety: **{r['protected_automation_safe']}**.",'',
            'Actual search queries:']
        lines += ['- ' + json.dumps(query if query else '(catalog)',ensure_ascii=False) for query in r['search_queries']]
        if r.get('strategy_validation',{}):
            lines+=['',f"Search certification: `{r['strategy_validation'].get('code',r['strategy_validation'].get('status'))}`."]
        lines += ['', 'Persisted phase/outcome notes:'] + ['- '+error for error in r['errors']]
        if r.get('observed_unreconciled_metrics'):
            raw=r['observed_unreconciled_metrics']
            lines+=['',f"Unreconciled observations only: accuracy {number(raw['accuracy'])}; outdated recall {number(raw['outdated_recall'])}, "
                f"precision {number(raw['outdated_precision'])}. These do not replace the frozen primary failure score."]
    diagnosis_path=ROOT/'evaluations'/(series+'-stop-diagnosis.json')
    if diagnosis_path.exists():
        diagnosis=json.loads(diagnosis_path.read_text(encoding='utf-8'))
        canonical=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        if diagnosis.get('canonical_report_sha256') != canonical:
            raise ValueError('Stop diagnosis does not match this report')
        lines+=['','## Read-only failure diagnosis','',diagnosis['summary'],'',
            f"Actual provider emission: **{diagnosis['provider_native_question_count']} native questions**, "
            f"with **{diagnosis['provider_native_emission_recall']*100:.1f}%** question recall against the five unavailable contexts. "
            f"Product-persisted questions: **{diagnosis['persisted_question_count']}**.",'',
            f"Frozen rule failure: `{diagnosis['failed_rule']}`. Catalog event **{diagnosis['catalog_event_seq']}** "
            f"preceded the first document retrieval at event **{diagnosis['first_document_retrieval_seq']}**.",'',
            'The raw frozen result JSON is unchanged. Provider emission is separate diagnostic evidence; '
            'it does not convert the failed product run into a successful clarification or final reconciliation.','']
    lines+=['','## Interpretation','',
        'Primary metrics use reconciled final labels. Failed/unreconciled findings are excluded from trusted predictions, '
        'so missing classifications remain misses; provisional observations are retained separately in JSON. '
        'Undefined precision is N/A with an explicit sample count. An incomplete conversation can still have valid '
        'per-document reconciliation, five honest UNKNOWN findings and a protected manual-only CRM correction.','',
        'The parent-step number is a reserved allowance. Observed LLM rounds are distinct stream correlation IDs, '
        'not a provider-certified consumed-parent-step counter. Nested MCP calls are measured separately.','',
        'These are five empirical executions on one 24-document fictional corpus, not a general production reliability guarantee.','',
        '## Evidence and reproduction','',
        f'- Frozen configuration: `evaluations/{series}-protocol.json`.',
        f'- Machine-readable report: `evaluations/{series}-results.json`.',
        f'- Raw canonical run/finding/tool/audit evidence: five isolated `data/{series}/evaluation-*.db` files.',
        f'- Private factual fixture, dispatch markers, provider read-backs and logs: `.tools/{series}/`.',
        '- Redacted execution traces: `runs/impact_<phase-run-id>.json`.',
        '- UI: `http://127.0.0.1:8401/#/reliability`.',
        f'- Regenerate this Markdown: `python -B -m scripts.render_reliability_report --series {series}`.',
        '- Scoring definitions, safety limits and preparation commands: `docs/RELIABILITY.md`.','']
    return '\n'.join(lines)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--series',required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'reliability-v[1-9][0-9]*',args.series): parser.error('Invalid series')
    report=json.loads((ROOT/'evaluations'/(args.series+'-results.json')).read_text(encoding='utf-8'))
    if report.get('series_id',args.series)!=args.series: raise ValueError('Report series mismatch')
    output=ROOT/'evaluations'/(args.series+'-report.md')
    output.write_text(render(report),encoding='utf-8')
    print('Rendered',output.relative_to(ROOT))
