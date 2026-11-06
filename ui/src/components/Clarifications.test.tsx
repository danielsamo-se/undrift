import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { Conversation } from '../api/types';
import { reviewerSession } from '../api/session';
import { api } from '../api/client';
import { ConversationQuestions } from './Clarifications';

afterEach(() => { cleanup(); reviewerSession.clear(); vi.restoreAllMocks(); });
function conversation(): Conversation {
  return { id: 'run-root', thread_id: 'real-thread', state: 'awaiting_answers', max_turns: 3,
    max_total_steps: 80, max_reported_cost: '2', phases: [{ run_id: 'run-root', ordinal: 1,
      message_id: 'real-message', state: 'awaiting_answers', cost: { total: '0.2' }, questions: [{
        id: 'question-one', document_id: 'help-002', document_version: 1, policy_version: 2,
        prompt: '[document:help-002;version:1] What did the offer promise?',
        options: ['It promised 60 days.', 'The terms cannot be established.'], evidence: 'Refer to original terms.',
        answer: null, answered_by: null,
      }] }] };
}

it('shows actual native prompt/options/document context and offers the existing unlock flow', () => {
  render(<ConversationQuestions conversation={conversation()} reload={vi.fn()} />);
  expect(screen.getByText(/What did the offer promise/)).toBeTruthy();
  expect(screen.getByText('Refer to original terms.')).toBeTruthy();
  expect(screen.getAllByRole('radio')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Unlock reviewer session to answer' }));
  expect(screen.getByLabelText('Review token')).toBeTruthy();
  expect(screen.queryByRole('button', { name: /continue|publish|approve/i })).toBeNull();
});

it('sends an option index through the authenticated API and reloads canonical state', async () => {
  reviewerSession.set('private-review-token', 'reviewer');
  const answer = vi.spyOn(api, 'answerQuestion').mockResolvedValue({ question_id: 'question-one', status: 'answered' });
  const reload = vi.fn();
  render(<ConversationQuestions conversation={conversation()} reload={reload} />);
  fireEvent.click(screen.getByLabelText('It promised 60 days.'));
  fireEvent.click(screen.getByRole('button', { name: 'Save factual answer' }));
  await waitFor(() => expect(answer).toHaveBeenCalledWith('question-one', { option: 0 }));
  expect(reload).toHaveBeenCalledOnce();
  expect(document.body.textContent).not.toContain('private-review-token');
});

it('free text replaces the option and handles stale/duplicate errors without claiming success', async () => {
  reviewerSession.set('private-review-token', 'reviewer');
  const answer = vi.spyOn(api, 'answerQuestion').mockRejectedValue(new Error('clarification context is stale'));
  const reload = vi.fn();
  render(<ConversationQuestions conversation={conversation()} reload={reload} />);
  fireEvent.click(screen.getByLabelText('It promised 60 days.'));
  fireEvent.change(screen.getByLabelText('Or provide factual context'), { target: { value: 'Original offer not found.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save factual answer' }));
  await waitFor(() => expect(answer).toHaveBeenCalledWith('question-one', { text: 'Original offer not found.' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'clarification context is stale');
  expect(reload).not.toHaveBeenCalled();
});

it('shows saved answers and the paid-authorization boundary, without an automatic continuation action', () => {
  const conv = conversation();
  conv.state = 'ready';
  conv.phases[0]!.questions[0]!.answer = 'It promised 60 days.';
  conv.phases[0]!.questions[0]!.answered_by = 'reviewer:local';
  render(<ConversationQuestions conversation={conv} reload={vi.fn()} />);
  expect(screen.getByRole('status').textContent).toContain('Awaiting explicitly authorized paid continuation');
  expect(screen.getByText(/Answer recorded/)).toBeTruthy();
  expect(screen.queryByRole('radio')).toBeNull();
});

it('shows exhausted continuation budgets instead of promising execution', () => {
  const conv = conversation();
  conv.state = 'ready';
  conv.continuation_blocker = 'Conversation turn limit reached.';
  render(<ConversationQuestions conversation={conv} reload={vi.fn()} />);
  expect(screen.getByRole('alert').textContent).toContain('Conversation turn limit reached.');
  expect(screen.queryByRole('status')).toBeNull();
});
