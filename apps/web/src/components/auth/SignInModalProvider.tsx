"use client";

import {
  createContext,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useSearchParams } from "next/navigation";

import { SignInDialog } from "@/components/auth/SignInDialog";
import { usePathname, useRouter } from "@/i18n/navigation";
import { safeAuthCallbackUrl } from "@/lib/auth/callback-url";

type SignInModalApi = {
  openSignIn: (options?: { callbackUrl?: string }) => void;
};

const SignInModalContext = createContext<SignInModalApi | null>(null);

export function useSignInModal(): SignInModalApi {
  const value = useContext(SignInModalContext);
  if (!value) {
    throw new Error("useSignInModal must be used within SignInModalProvider");
  }
  return value;
}

export function SignInModalProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [callbackUrl, setCallbackUrl] = useState("");

  const openSignIn = useCallback((options?: { callbackUrl?: string }) => {
    setCallbackUrl(
      options?.callbackUrl ? safeAuthCallbackUrl(options.callbackUrl, "") : "",
    );
    setOpen(true);
  }, []);

  const api = useMemo(() => ({ openSignIn }), [openSignIn]);

  return (
    <SignInModalContext.Provider value={api}>
      {children}
      {open ? (
        <SignInDialog
          callbackUrl={callbackUrl}
          onClose={() => setOpen(false)}
        />
      ) : null}
      <Suspense fallback={null}>
        <SignInQueryOpener openSignIn={openSignIn} />
      </Suspense>
    </SignInModalContext.Provider>
  );
}

function SignInQueryOpener({
  openSignIn,
}: {
  openSignIn: SignInModalApi["openSignIn"];
}) {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const consumed = useRef<string | null>(null);

  useEffect(() => {
    const flag = searchParams.get("signin");
    if (flag !== "1" && flag !== "true") {
      consumed.current = null;
      return;
    }
    const key = searchParams.toString();
    if (consumed.current === key) return;
    consumed.current = key;
    openSignIn({
      callbackUrl:
        searchParams.get("callbackUrl") ??
        searchParams.get("redirect_url") ??
        searchParams.get("next") ??
        "",
    });
    const next = new URLSearchParams(searchParams.toString());
    next.delete("signin");
    next.delete("callbackUrl");
    next.delete("redirect_url");
    next.delete("next");
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [openSignIn, pathname, router, searchParams]);

  return null;
}
