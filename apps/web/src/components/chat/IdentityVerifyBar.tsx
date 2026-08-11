"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import {
  getPublicIdentityStatus,
  requestPublicIdentityChallenge,
  verifyPublicIdentity,
  type IdentityStatus,
} from "@/lib/agentos/client";

type Props = {
  tenantSlug: string;
  sessionId: string | undefined;
  guestId: string;
  compact?: boolean;
};

export function IdentityVerifyBar({
  tenantSlug,
  sessionId,
  guestId,
  compact = false,
}: Props) {
  const tCommon = useTranslations("common");
  const [status, setStatus] = useState<IdentityStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [debugCode, setDebugCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setStatus(null);
      return;
    }
    const controller = new AbortController();
    setCodeSent(false);
    setCode("");
    setDebugCode(null);
    setError(null);
    setOpen(false);
    void getPublicIdentityStatus({
      tenantSlug,
      sessionId,
      guestId,
      signal: controller.signal,
    })
      .then(setStatus)
      .catch(() => setStatus({ verified: false, endUserId: null, email: null, displayName: null, metadata: null }));
    return () => controller.abort();
  }, [guestId, sessionId, tenantSlug]);

  async function sendCode() {
    if (!sessionId || !email.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await requestPublicIdentityChallenge({
        tenantSlug,
        sessionId,
        email: email.trim(),
        guestId,
      });
      setCodeSent(true);
      setDebugCode(result.debugCode);
      setEmail(result.email);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not send code");
    } finally {
      setBusy(false);
    }
  }

  async function confirmCode() {
    if (!sessionId || !email.trim() || !code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await verifyPublicIdentity({
        tenantSlug,
        sessionId,
        email: email.trim(),
        code: code.trim(),
        guestId,
      });
      setStatus(result);
      setOpen(false);
      setCodeSent(false);
      setCode("");
      setDebugCode(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Verification failed");
    } finally {
      setBusy(false);
    }
  }

  if (status?.verified) {
    return (
      <p
        className={`text-xs text-white/70 ${compact ? "mt-2" : "mt-3"}`}
        title="Verified for this chat session"
      >
        Verified as{" "}
        <span className="font-medium text-white">
          {status.displayName || status.email}
        </span>
      </p>
    );
  }

  return (
    <div className={compact ? "mt-2" : "mt-3"}>
      {!open ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-white/75 transition hover:border-white/30 hover:text-white"
        >
          Verify email
        </button>
      ) : (
        <div className="max-w-md space-y-2 rounded-xl border border-white/10 bg-black/20 p-3">
          <p className="text-xs text-white/70">
            Verify your email so this chat can use your profile securely.
          </p>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
            disabled={busy || codeSent}
            className="w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white placeholder:text-white/40 outline-none focus:border-[var(--tenant-accent)]"
          />
          {codeSent ? (
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder="6-digit code"
              disabled={busy}
              className="w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white placeholder:text-white/40 outline-none focus:border-[var(--tenant-accent)]"
            />
          ) : null}
          {debugCode ? (
            <p className="text-[11px] text-amber/90">Dev code: {debugCode}</p>
          ) : null}
          {error ? <p className="text-[11px] text-amber">{error}</p> : null}
          <div className="flex flex-wrap gap-2">
            {!codeSent ? (
              <button
                type="button"
                disabled={busy || !email.trim() || !sessionId}
                onClick={() => void sendCode()}
                className="rounded-lg border border-[var(--tenant-accent)]/50 bg-white/10 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              >
                {busy ? "Sending…" : "Send code"}
              </button>
            ) : (
              <button
                type="button"
                disabled={busy || code.trim().length < 4}
                onClick={() => void confirmCode()}
                className="rounded-lg border border-[var(--tenant-accent)]/50 bg-white/10 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              >
                {busy ? "Verifying…" : "Confirm"}
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setError(null);
              }}
              className="rounded-lg border border-white/15 px-3 py-1.5 text-xs text-white/65"
            >
              {tCommon("cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
