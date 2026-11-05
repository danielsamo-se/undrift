import { Lock, LockOpen } from "lucide-react";
import { useState } from "react";
import { ApiError, api } from "../api/client";
import { reviewerSession, useReviewerSession } from "../api/session";
import { Modal, Spinner } from "./ui";

export function UnlockDialog({ onClose }: { onClose: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const { reviewer } = await api.verifySession(token.trim());
      reviewerSession.set(token.trim(), reviewer);
      setToken("");
      onClose();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "That token was not accepted." : (err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Unlock reviewer session"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!token.trim() || busy} onClick={() => void submit()}>
            {busy ? <Spinner /> : <LockOpen size={13} />} Unlock
          </button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (token.trim()) void submit();
        }}
      >
        <label className="field">
          <span>Review token</span>
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="UNDRIFT_REVIEW_TOKEN"
            aria-invalid={Boolean(error)}
          />
        </label>
        {error && <p className="field-error">{error}</p>}
        <p className="muted small mt">
          Copy it without displaying it: <code>.\.venv\Scripts\python.exe cli.py copy-token</code>. The server verifies it; this tab
          keeps it in memory only, never in storage, cookies or the URL. Reload or Lock ends the session.
        </p>
      </form>
    </Modal>
  );
}

export function ReviewerControl() {
  const session = useReviewerSession();
  const [open, setOpen] = useState(false);
  return (
    <>
      {session ? (
        <button className="reviewer is-on" onClick={() => reviewerSession.clear()} title="Lock reviewer session">
          <span className="reviewer-dot" aria-hidden />
          <span className="reviewer-text">
            Reviewer <strong>{session.reviewer}</strong>
          </span>
          <Lock size={12} aria-hidden />
        </button>
      ) : (
        <button className="reviewer" onClick={() => setOpen(true)}>
          <Lock size={12} aria-hidden />
          <span className="reviewer-text">Reviewer locked</span>
        </button>
      )}
      {open && <UnlockDialog onClose={() => setOpen(false)} />}
    </>
  );
}
