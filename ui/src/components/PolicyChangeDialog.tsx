import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { Policy } from "../api/types";
import { Modal, PolicyVersion, Spinner } from "./ui";

export function PolicyChangeDialog({ policy, onClose, onChanged }: {
  policy: Policy;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [days, setDays] = useState(String(policy.voluntary_return_days));
  const [effective, setEffective] = useState(policy.effective_from);
  const [description, setDescription] = useState(policy.description);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const parsedDays = Number(days);
  const valid = Number.isInteger(parsedDays) && parsedDays >= 1 && parsedDays <= 365 && /^\d{4}-\d{2}-\d{2}$/.test(effective) && description.trim().length > 0;
  const unchanged =
    parsedDays === policy.voluntary_return_days && effective === policy.effective_from && description === policy.description;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.changePolicy(policy.id, { voluntary_return_days: parsedDays, effective_from: effective, description });
      onChanged();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Change the authoritative policy"
      onClose={() => !busy && onClose()}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!valid || unchanged || busy} onClick={() => void submit()}>
            {busy && <Spinner />} Create <PolicyVersion version={policy.version + 1} />
          </button>
        </>
      }
    >
      <p>
        Creates policy version P{policy.version + 1} in the local store. The deterministic prefilter runs immediately.
        Agent findings for P{policy.version} become stale; this does <strong>not</strong> start a paid agent run.
      </p>
      <div className="field-grid">
        <label className="field">
          <span>Voluntary return period (days)</span>
          <input type="number" min={1} max={365} value={days} onChange={(e) => setDays(e.target.value)} />
        </label>
        <label className="field">
          <span>Effective from</span>
          <input type="date" value={effective} onChange={(e) => setEffective(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>Authoritative policy text</span>
        <textarea rows={5} maxLength={4000} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      {error && <p className="field-error">{error}</p>}
    </Modal>
  );
}
