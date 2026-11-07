import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {JuryBanner,JuryGuide,JuryProvider} from './jury';
import {replayRequest} from './jury-mode';
import {ReviewDecision} from '../components/ReviewDecision';
import {api} from '../api/client';
import type {ProposalDetail} from '../api/types';

vi.mock('./jury-mode',()=>({PUBLIC_JURY:true,setReplay:vi.fn(),replayRequest:vi.fn()}));
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();window.location.hash='';});
it('clearly marks genuine history as replay and selects independent evidence archives',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({snapshot_id:'real-snapshot',captured_at:'2026-11-07T10:00:00Z',default_replay:'clarification',
    replays:[{id:'clarification',name:'Native clarification',database_id:'original-a',document_count:24},
             {id:'publishing',name:'Publishing',database_id:'original-b',document_count:4},
             {id:'autonomy-fixture',name:'Autonomy fixture',database_id:'fixture-c',document_count:3,kind:'fixture'}]})}));
  const detail={proposal:{status:'pending_review'},checks:{document_unchanged:true,policy_current:true}} as ProposalDetail;
  render(<JuryProvider><JuryBanner/><JuryGuide/><ReviewDecision detail={detail} onChanged={vi.fn()}/></JuryProvider>);
  expect(await screen.findByText('Recorded evidence · Read-only jury demo')).toBeTruthy();
  expect(screen.getByText(/No agents run from this site/)).toBeTruthy();
  expect(screen.getByText('One policy changes')).toBeTruthy();
  expect(screen.getAllByRole('listitem').length).toBeGreaterThanOrEqual(9);
  expect(screen.getAllByText('Local test fixture · not a Cominty run').length).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText('Evidence archive'),{target:{value:'publishing'}});
  await waitFor(()=>expect(screen.getByText('original-b')).toBeTruthy());
  expect(screen.queryByRole('button',{name:/^(approve|unlock reviewer|start agent|apply locally)$/i})).toBeNull();
  expect(screen.getByText(/Read-only replay. Approving/)).toBeTruthy();
  await expect(api.verifySession('private-token')).rejects.toMatchObject({status:403,code:'jury_readonly'});
  expect(replayRequest).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Evidence archive'),{target:{value:'autonomy-fixture'}});
  expect(await screen.findByText('Local test fixture · Read-only')).toBeTruthy();
  expect(screen.getByText(/not a Cominty agent run/)).toBeTruthy();
});
