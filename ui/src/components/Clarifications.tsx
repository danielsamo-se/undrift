import { useState } from 'react';
import { api } from '../api/client';
import { useReviewerSession } from '../api/session';
import type { Conversation, NativeQuestion } from '../api/types';
import { useResource } from '../lib/hooks';
import { useLive } from '../lib/live';
import { UnlockDialog } from './ReviewerSession';
import { useJury } from '../lib/jury';

function Question({ question, ready, onAnswered }: { question: NativeQuestion; ready: boolean; onAnswered: () => void }) {
  const session = useReviewerSession();
  const [unlock, setUnlock] = useState(false);
  const [choice, setChoice] = useState<number | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await api.answerQuestion(question.id, choice === null ? { text: text.trim() } : { option: choice });
      onAnswered();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  return <div className="card" style={{ padding: 16, marginBottom: 8 }}>
    <strong>{question.document_id} · v{question.document_version} · policy v{question.policy_version}</strong>
    <p>{question.prompt}</p>
    <details><summary>Retrieved document context</summary><p className="muted small">{question.evidence}</p></details>
    {question.answer !== null ? <p>Answer recorded: {question.answer} — {question.answered_by}</p> : <>
      {question.options.map((option, index) => <label className="field" key={index}>
        <span><input type="radio" name={question.id} checked={choice === index} disabled={!ready || busy}
          onChange={() => { setChoice(index); setText(''); }} /> {option}</span>
      </label>)}
      <label className="field"><span>Or provide factual context</span><textarea value={text} maxLength={4000}
        disabled={!ready || busy} onChange={e => { setChoice(null); setText(e.target.value); }} /></label>
      {!ready ? <p className="muted small">Question retained as evidence. This phase is not accepting answers.</p> : !session ? <button className="btn" onClick={() => setUnlock(true)}>Unlock reviewer session to answer</button> :
        <button className="btn btn-primary" disabled={!ready || busy || (choice === null && !text.trim())}
          onClick={() => void submit()}>{busy ? 'Saving…' : 'Save factual answer'}</button>}
    </>}
    {error && <p role="alert" className="field-error">{error}</p>}
    {unlock && <UnlockDialog onClose={() => setUnlock(false)} />}
  </div>;
}

export function ConversationQuestions({ conversation, reload }: { conversation: Conversation; reload: () => void }) {
  const jury=useJury();
  return <section className="panel" aria-label="Native agent clarification" style={{ padding: 16, marginBottom: 16 }}>
    <h2>Agent clarification · {conversation.state.replaceAll('_', ' ')}</h2>
    <p className="muted small">Conversation {conversation.id} · {conversation.phases.length}/{conversation.max_turns} turns ·
      {conversation.max_total_steps} total reserved parent steps · reported cost ceiling {conversation.max_reported_cost} (platform units).</p>
    <p className="muted small">Factual answers do not approve corrections or authorize paid execution.
      Once all answers are saved, an operator must separately authorize one continuation in the same Cominty conversation.</p>
    {['failed', 'expired', 'interrupted'].includes(conversation.state) && conversation.continuation_blocker &&
      <p role="alert">Continuation blocked: {conversation.continuation_blocker}</p>}
    {conversation.phases.map(phase => <div key={phase.run_id}>
      <p className="muted small">Phase {phase.ordinal} · {phase.state.replaceAll('_', ' ')} · {phase.run_id}
        {phase.cost && ` · reported cost ${phase.cost.total ?? 'unavailable'}`}</p>
      {phase.questions.map(q => <Question key={q.id} question={q}
        ready={!jury.readonly && conversation.state === 'awaiting_answers'} onAnswered={reload} />)}
    </div>)}
    {conversation.state === 'ready' && (conversation.continuation_blocker ?
      <p role="alert">Answers saved. Continuation blocked: {conversation.continuation_blocker}</p> :
      <p role="status">Answers saved. Awaiting explicitly authorized paid continuation.</p>)}
  </section>;
}

export function Clarifications() {
  const { latestSeq } = useLive();
  const { data, error, reload } = useResource(signal => api.conversations(signal), latestSeq);
  if (error) return <p role="alert">Clarification state unavailable: {error.message}</p>;
  return <>{data?.conversations.filter(c => c.phases.some(p => p.questions.length)).map(c =>
    <ConversationQuestions key={c.id} conversation={c} reload={reload} />)}</>;
}
