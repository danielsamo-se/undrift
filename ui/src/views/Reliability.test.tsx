import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReliabilityReport, EvaluationRun } from '../api/types';
import { Reliability, ReliabilityView } from './Reliability';
import { api } from '../api/client';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function report(): ReliabilityReport {
  return { version:'reliability-v1',state:'completed',protocol_sha256:'frozen-hash',results:[],
    aggregate:{planned_conversations:5,recorded_conversations:0,not_started:5,reconciled_conversations:0,
      message_count:0,reported_total_cost:'0',statistics:{}} };
}
it('shows not-started slots and undefined precision without inventing success', () => {
  render(<Reliability report={report()} />);
  expect(screen.getByRole('heading',{name:'Reliability'})).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain('5 planned conversations have not started');
  expect(screen.getAllByText('N/A').length).toBeGreaterThan(0);
  expect(screen.queryByText('Real Cominty execution')).toBeNull();
});
it('keeps a failed real run visible with missing-label penalties and stop reason', () => {
  const data=report(); data.state='stopped'; data.stop_reason='Provider execution failed';
  data.aggregate.recorded_conversations=1; data.aggregate.not_started=4;
  const run: EvaluationRun={slot:1,root_run_id:'run-failed',thread_id:'thread',status:'failed',reconciled:false,
    evidence_kind:'real_cominty',database:'data/eval.db',database_id:'identity',phase_run_ids:['run-failed'],message_ids:['message'],message_count:1,
    metrics:{accuracy:0,outdated_recall:0,outdated_precision:null,unknown_recall:0},observed_unreconciled_metrics:{accuracy:1},
    native_question_count:0,answered_count:0,native_question_integrity:true,protected_automation_safe:true,
    mcp_calls:{get_document:3},mcp_error_count:0,search_queries:[],reported_cost:'.1',duration_s:30,
    reserved_parent_steps:40,observed_llm_rounds:2,proposal_count:0,errors:['Agent failed'],misclassified_document_ids:['a'],final_retrieval_complete:false};
  data.results=[run]; render(<Reliability report={data} />);
  expect(screen.getByRole('alert').textContent).toContain('Provider execution failed');
  expect(screen.getByText('Real Cominty execution')).toBeTruthy();
  expect(screen.getByText('Unreconciled')).toBeTruthy();
  expect(screen.getByText(/Provisional observations exist/)).toBeTruthy();
  expect(screen.getByText(/not a certified consumed-step count/)).toBeTruthy();
});

it('separates native provider emission from rejected product persistence', () => {
  const data=report(); data.state='stopped'; data.stop_reason='';
  data.diagnosis={summary:'Search leads followed after catalog.',provider_native_question_count:5,
    persisted_question_count:0,provider_native_emission_recall:1,failed_rule:'lead_retrieval_before_catalog',
    catalog_event_seq:29,first_document_retrieval_seq:31};
  render(<Reliability report={data} />);
  expect(screen.getByRole('alert').textContent).toContain('Search leads followed after catalog');
  expect(screen.getByText(/Cominty emitted 5 genuine native questions/)).toBeTruthy();
  expect(screen.getByText(/Frozen persistence scores remain unchanged/)).toBeTruthy();
});

it('labels the active series and archived study without merging results', async () => {
  const current={...report(),series_id:'reliability-v2',active_series_id:'reliability-v2',
    available_series:['reliability-v1','reliability-v2'],archived:false,state:'prepared'};
  const archived={...current,series_id:'reliability-v1',archived:true,state:'stopped',stop_reason:'Original failure'};
  const request=vi.spyOn(api,'reliability').mockImplementation(async (_signal,series) => series==='reliability-v1' ? archived : current);
  render(<ReliabilityView />);
  expect(await screen.findByText('reliability-v2',{selector:'strong'})).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Evaluation series'),{target:{value:'reliability-v1'}});
  await waitFor(() => expect(request).toHaveBeenLastCalledWith(expect.any(AbortSignal),'reliability-v1'));
  expect(await screen.findByText('reliability-v1',{selector:'strong'})).toBeTruthy();
  expect(screen.getByText(/Archived study/)).toBeTruthy();
  expect(screen.getByRole('alert').textContent).toContain('Original failure');
});
