"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { DomainChatTarget } from "@/lib/api/admin";

type DeskChatDraftContextValue = {
  liveTradingAvailable: boolean;
  pendingTeamId: string | null;
  pendingDraft: string | null;
  applyToLiveTrading: (text: string) => boolean;
  consumePending: () => void;
};

const DeskChatDraftContext = createContext<DeskChatDraftContextValue | null>(null);

export function DeskChatDraftProvider({
  targets,
  children,
}: {
  targets: DomainChatTarget[];
  children: ReactNode;
}) {
  const liveTarget = useMemo(
    () => targets.find((row) => row.slug === "live-trading") ?? null,
    [targets],
  );
  const [pendingTeamId, setPendingTeamId] = useState<string | null>(null);
  const [pendingDraft, setPendingDraft] = useState<string | null>(null);

  const applyToLiveTrading = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!liveTarget || !trimmed) return false;
      setPendingTeamId(liveTarget.id);
      setPendingDraft(trimmed);
      document.getElementById("desk-chat")?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
      return true;
    },
    [liveTarget],
  );

  const consumePending = useCallback(() => {
    setPendingTeamId(null);
    setPendingDraft(null);
  }, []);

  const value = useMemo(
    () => ({
      liveTradingAvailable: Boolean(liveTarget),
      pendingTeamId,
      pendingDraft,
      applyToLiveTrading,
      consumePending,
    }),
    [
      applyToLiveTrading,
      consumePending,
      liveTarget,
      pendingDraft,
      pendingTeamId,
    ],
  );

  return (
    <DeskChatDraftContext.Provider value={value}>
      {children}
    </DeskChatDraftContext.Provider>
  );
}

export function useDeskChatDraftOptional() {
  return useContext(DeskChatDraftContext);
}
