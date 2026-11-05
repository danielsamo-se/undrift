import { api } from "../api/client";
import { useResource } from "./hooks";
import { useLive } from "./live";

export const POLICY_ID = "shiftly-returns";

export function useImpact() {
  const { latestSeq } = useLive();
  return useResource((signal) => api.impact(POLICY_ID, signal), latestSeq);
}

export function useProposals() {
  const { latestSeq } = useLive();
  return useResource((signal) => api.proposals(undefined, signal), latestSeq);
}

export function useRuns() {
  const { latestSeq } = useLive();
  return useResource((signal) => api.runs(signal), latestSeq);
}
