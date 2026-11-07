import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { JuryBanner, JuryGuide, JuryProvider, STEPS, TourBar } from './jury';

vi.mock('./jury-mode', () => ({ PUBLIC_JURY: true, setReplay: vi.fn(), replayRequest: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, '', '/'); });

const IDS = ['discovery', 'clarification', 'publishing', 'guard', 'autonomy', 'autonomy-live', 'autonomy-fixture',
  'evaluation-1', 'evaluation-2', 'evaluation-3', 'evaluation-4', 'evaluation-5', 'reliability-v1', 'future-archive'];
const CATALOG = { snapshot_id: 's', captured_at: '2026-11-07T10:00:00Z', default_replay: 'clarification',
  replays: IDS.map(id => ({ id, name: `Name ${id}`, description: `About ${id}`, database_id: `db-${id}`, document_count: 1,
    kind: id === 'autonomy-fixture' ? 'fixture' : 'recorded' })) };
const url = () => new URL(window.location.href);

it('groups every archive, including unknown ones, and labels its evidence type', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => CATALOG }));
  window.history.replaceState(null, '', '/?replay=autonomy-live#/overview');
  render(<JuryProvider><JuryBanner /></JuryProvider>);
  const select = await screen.findByLabelText('Evidence archive') as HTMLSelectElement;
  const groups = [...select.querySelectorAll('optgroup')].map(g => [g.label, g.querySelectorAll('option').length]);
  expect(groups).toEqual([['Main walkthrough', 4], ['Channel autonomy', 3], ['Reliability evaluation', 6], ['Other', 1]]);
  expect(select.querySelectorAll('option')).toHaveLength(14);
  expect(screen.getByTestId('archive-description').textContent).toMatch(/Real Cominty run · failed closed.*About autonomy-live/);
  expect(select.querySelector('option[value="autonomy-fixture"]')?.textContent).toMatch(/Local fixture · not a Cominty run/);
  fireEvent.change(select, { target: { value: 'evaluation-2' } });
  await waitFor(() => expect(url().searchParams.get('replay')).toBe('evaluation-2'));
  expect(window.location.hash).toBe('#/overview');
});

it('guides step by step across archives with previous, next, paused state, all steps and history', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => CATALOG }));
  window.history.replaceState(null, '', '/?replay=clarification#/tour');
  render(<JuryProvider><JuryGuide /><TourBar /></JuryProvider>);
  fireEvent.click(await screen.findByRole('button', { name: 'Start the walkthrough' }));
  await waitFor(() => expect(screen.getByTestId('tour-bar').textContent).toMatch(/Step 1 of 10/));
  expect(url().searchParams.get('replay')).toBe('discovery');
  expect(window.location.hash).toBe('#/overview');
  expect(screen.getByRole('button', { name: 'No previous step' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: /^Next step: Undrift finds the drift/ }));
  await waitFor(() => expect(screen.getByTestId('tour-bar').textContent).toMatch(/Step 2 of 10/));
  expect(window.location.hash).toBe('#/impact');
  fireEvent.click(screen.getByRole('button', { name: 'Step 8: Guard: ALLOW and BLOCK' }));
  await waitFor(() => expect(url().searchParams.get('replay')).toBe('guard'));
  expect(window.location.hash).toBe('#/guard/gchk-5ce8448d76f85805');
  expect(url().searchParams.get('step')).toBe('8');
  window.location.hash = '#/activity';
  await waitFor(() => expect(screen.getByTestId('tour-bar').textContent).toMatch(/paused at step 8/));
  fireEvent.click(screen.getByRole('button', { name: 'Return to step 8' }));
  await waitFor(() => expect(window.location.hash).toBe('#/guard/gchk-5ce8448d76f85805'));
  fireEvent.click(screen.getByRole('button', { name: 'All steps' }));
  await waitFor(() => expect(window.location.hash).toBe('#/tour'));
  expect(url().searchParams.get('step')).toBeNull();
});

it('keeps ten distinct steps, with no two consecutive steps opening the same screen', () => {
  expect(STEPS).toHaveLength(10);
  expect(STEPS.map(s => s.n)).toEqual(['01', '02', '03', '04', '05', '06', '07', '08', '09', '10']);
  for (let i = 1; i < STEPS.length; i++) {
    const a = STEPS[i - 1]!, b = STEPS[i]!;
    expect(`${a.replay}/${a.view}/${a.param ?? ''}`).not.toBe(`${b.replay}/${b.view}/${b.param ?? ''}`);
  }
  expect(STEPS.every(s => s.notice.length > 40)).toBe(true);
});
