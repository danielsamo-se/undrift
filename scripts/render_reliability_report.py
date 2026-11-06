"""Render the preserved JSON evaluation result as Markdown; no providers or DB writes."""
import json
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]

def render(report):
    a=report['aggregate']
    def number(value,percent=True):
        return 'N/A' if value is None else f'{value*100:.2f}%' if percent else f'{value:.2f}'
    lines=['# Undrift Reliability Evaluation v1','',
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
    if report.get('stop_reason'): lines+=['',f"Stop reason: **{report['stop_reason']}**. No automatic retry was started."]
    diagnosis_path=ROOT/'evaluations/reliability-v1-stop-diagnosis.json'
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
        '- Frozen configuration: `evaluations/reliability-v1-protocol.json`.',
        '- Machine-readable report: `evaluations/reliability-v1-results.json`.',
        '- Raw canonical run/finding/tool/audit evidence: five isolated `data/reliability-v1/evaluation-*.db` files.',
        '- Private factual fixture, dispatch markers, provider read-backs and logs: `.tools/reliability-v1/`.',
        '- Redacted execution traces: `runs/impact_<phase-run-id>.json`.',
        '- UI: `http://127.0.0.1:8401/#/reliability`.',
        '- Regenerate this Markdown: `python -B -m scripts.render_reliability_report`.',
        '- Scoring definitions, safety limits and preparation commands: `docs/RELIABILITY.md`.','']
    return '\n'.join(lines)

if __name__=='__main__':
    report=json.loads((ROOT/'evaluations/reliability-v1-results.json').read_text(encoding='utf-8'))
    output=ROOT/'evaluations/reliability-v1-report.md'
    output.write_text(render(report),encoding='utf-8')
    print('Rendered',output.relative_to(ROOT))
