"use client";

import { getSession, signIn } from "next-auth/react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { AdminFormDialog } from "@/components/ui/AdminFormDialog";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import { EyeIcon, EyeOffIcon } from "@/components/ui/icons";
import { getOnboardingStatus, getWorkspaceInfo } from "@/lib/api/admin";
import { keycloakResetCredentialsUrl } from "@/lib/auth/keycloak-public";
import {
  localePrefixedPath,
  resolvePostLoginHref,
} from "@/lib/auth/post-login";

export function SignInDialog({
  callbackUrl,
  onClose,
}: {
  callbackUrl: string;
  onClose: () => void;
}) {
  const t = useTranslations("auth");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await signIn("credentials", {
        username,
        password,
        redirect: false,
        callbackUrl,
      });
      if (result?.error || result?.ok === false) {
        setError(t("invalidCredentials"));
        setBusy(false);
        return;
      }
      const session = (await getSession()) as {
        accessToken?: string;
        orgRole?: string;
      } | null;
      if (!session?.accessToken) {
        setError(t("invalidCredentials"));
        setBusy(false);
        return;
      }
      const dest = await resolvePostLoginHref({
        accessToken: session.accessToken,
        orgRole: session.orgRole,
        callbackUrl,
        loadWorkspace: async () => {
          const [status, workspace] = await Promise.all([
            getOnboardingStatus(session.accessToken!).catch(() => null),
            getWorkspaceInfo(session.accessToken!),
          ]);
          return {
            slug: workspace.slug || status?.tenant_slug || null,
            can_administer: workspace.can_administer,
          };
        },
      });
      window.location.assign(localePrefixedPath(locale, dest));
    } catch {
      setError(t("invalidCredentials"));
      setBusy(false);
    }
  }

  return (
    <AdminFormDialog
      title={t("signInAccountTitle")}
      titleId="atlas-sign-in-title"
      onClose={onClose}
      showCloseButton
    >
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div>
          <Label htmlFor="atlas-sign-in-username">{t("usernameOrEmail")}</Label>
          <Input
            id="atlas-sign-in-username"
            name="username"
            type="text"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="atlas-sign-in-password">{t("password")}</Label>
          <div className="relative">
            <Input
              id="atlas-sign-in-password"
              name="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              className="pr-10"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <button
              type="button"
              className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-muted hover:text-ink"
              onClick={() => setShowPassword((current) => !current)}
              aria-label={showPassword ? t("hidePassword") : t("showPassword")}
            >
              {showPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
        </div>
        <div className="flex justify-end">
          <a
            href={keycloakResetCredentialsUrl()}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-teal hover:text-teal-bright"
          >
            {t("forgotPassword")}
          </a>
        </div>
        {error ? <p className="text-sm text-rose">{error}</p> : null}
        <Button
          type="submit"
          variant="accent"
          className="w-full"
          disabled={busy || !username.trim() || !password}
        >
          {busy ? t("signingIn") : tCommon("signIn")}
        </Button>
      </form>
    </AdminFormDialog>
  );
}
