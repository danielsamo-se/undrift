import type { Category, ImpactDocument } from "../api/types";

export type MapStatus = "outdated" | "valid" | "commitment" | "unrelated" | "unknown" | "unanalyzed";

export interface StatusMeta {
  key: MapStatus;
  label: string;
  short: string;
  description: string;
}

export const STATUS: Record<MapStatus, StatusMeta> = {
  outdated: {
    key: "outdated",
    label: "Outdated",
    short: "Outdated",
    description: "Contradicts the current policy for new purchases. Needs a correction.",
  },
  valid: {
    key: "valid",
    label: "Valid, distinct rule",
    short: "Valid",
    description: "States a separate rule (e.g. statutory withdrawal right) that must stay unchanged.",
  },
  commitment: {
    key: "commitment",
    label: "Existing commitment",
    short: "Commitment",
    description: "A promise made before the change took effect. It stays valid.",
  },
  unrelated: {
    key: "unrelated",
    label: "Unrelated",
    short: "Unrelated",
    description: "Not affected by this policy.",
  },
  unknown: {
    key: "unknown",
    label: "Unknown",
    short: "Unknown",
    description: "Return-policy applicability needs clarification, or required retrieval failed.",
  },
  unanalyzed: {
    key: "unanalyzed",
    label: "Not analysed",
    short: "Not analysed",
    description: "No agent has classified this document yet.",
  },
};

export const STATUS_ORDER: MapStatus[] = ["outdated", "unknown", "commitment", "valid", "unrelated", "unanalyzed"];

const BY_CATEGORY: Record<Category, MapStatus> = {
  OUTDATED: "outdated",
  VALID_DISTINCT: "valid",
  EXISTING_COMMITMENT: "commitment",
  UNRELATED: "unrelated",
  UNKNOWN: "unknown",
};

export function statusOf(doc: Pick<ImpactDocument, "finding">): MapStatus {
  const category = doc.finding?.category;
  if (doc.finding?.verified_conclusion === false && category !== "OUTDATED") return "unknown";
  return category && category in BY_CATEGORY ? BY_CATEGORY[category] : category ? "unknown" : "unanalyzed";
}

export function isStale(doc: Pick<ImpactDocument, "finding">): boolean {
  return Boolean(doc.finding && !doc.finding.current);
}

export const PREFILTER_LABEL: Record<string, string> = {
  actionable: "states a superseded period",
  protected: "states a superseded period in a protected sentence",
  not_stale: "no superseded period found",
};
