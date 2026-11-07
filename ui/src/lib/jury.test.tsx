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
             {id:'publishing',name:'Publishing',database_id:'original-b',document_count:4}]})}));
  const detail={proposal:{status:'pending_review'},checks:{document_unchanged:true,policy_current:true}} as ProposalDetail;
  render(<JuryProvider><JuryBanner/><JuryGuide/><ReviewDecision detail={detail} onChanged={vi.fn()}/></JuryProvider>);
  expect(await screen.findByText('Historical replay · Read-only jury demo')).toBeTruthy();
  expect(screen.getByText(/No agents are running/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Evidence archive'),{target:{value:'publishing'}});
  await waitFor(()=>expect(screen.getByText('original-b')).toBeTruthy());
  expect(screen.queryByRole('button',{name:/^(approve|unlock reviewer|start agent|apply locally)$/i})).toBeNull();
  expect(screen.getByText(/Read-only replay. Approving/)).toBeTruthy();
  await expect(api.verifySession('private-token')).rejects.toMatchObject({status:403,code:'jury_readonly'});
  expect(replayRequest).not.toHaveBeenCalled();
});
