import { reviewerSession } from "./session";
import type {
  EventsPage,
  ImpactSnapshot,
  Policy,
  Proposal,
  ProposalDetail,
  ProposalStatus,
  RunDetail,
  RunSummary,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  token?: string | null;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (options.body !== undefined) headers["content-type"] = "application/json";
  if (options.token) headers.authorization = `Bearer ${options.token}`;
  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      credentials: "omit",
      cache: "no-store",
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "network_error", "The Undrift API is not reachable. Is api.py running?");
  }
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const body = (data ?? {}) as { error?: string; message?: string; details?: Record<string, unknown> };
    throw new ApiError(response.status, body.error ?? "http_error", body.message ?? `HTTP ${response.status}`, body.details);
  }
  return data as T;
}

function authorized<T>(path: string, body: unknown): Promise<T> {
  const token = reviewerSession.token();
  if (!token) {
    return Promise.reject(new ApiError(401, "locked", "Unlock the reviewer session first."));
  }
  return request<T>(path, { method: "POST", body, token }).catch((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) reviewerSession.clear();
    throw err;
  });
}

const enc = encodeURIComponent;

export const api = {
  impact: (policyId: string, signal?: AbortSignal) =>
    request<ImpactSnapshot>(`/api/policies/${enc(policyId)}/impact`, { signal }),
  eventsTail: (tail: number) => request<EventsPage>(`/api/events?tail=${tail}`),
  eventsAfter: (after: number, limit = 500) => request<EventsPage>(`/api/events?after=${after}&limit=${limit}`),
  runs: (signal?: AbortSignal) => request<{ runs: RunSummary[] }>("/api/runs", { signal }),
  run: (id: string, signal?: AbortSignal) => request<{ run: RunDetail }>(`/api/runs/${enc(id)}`, { signal }),
  proposals: (status?: ProposalStatus, signal?: AbortSignal) =>
    request<{ proposals: Proposal[] }>(`/api/proposals${status ? `?status=${status}` : ""}`, { signal }),
  proposal: (id: string, signal?: AbortSignal) => request<ProposalDetail>(`/api/proposals/${enc(id)}`, { signal }),
  verifySession: (token: string) =>
    request<{ reviewer: string }>("/api/session", { method: "POST", token, body: {} }),
  approve: (id: string, note: string | null) =>
    authorized<{ proposal: Proposal }>(`/api/proposals/${enc(id)}/approve`, { note }),
  reject: (id: string, note: string | null) =>
    authorized<{ proposal: Proposal }>(`/api/proposals/${enc(id)}/reject`, { note }),
  apply: (id: string) => authorized<{ proposal: Proposal }>(`/api/proposals/${enc(id)}/apply`, {}),
  changePolicy: (policyId: string, body: { expected_version: number; voluntary_return_days: number; effective_from: string; description: string }) =>
    authorized<{ policy: Policy }>(`/api/policies/${enc(policyId)}/versions`, body),
};
