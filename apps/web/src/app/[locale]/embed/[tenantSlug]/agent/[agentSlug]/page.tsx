import { notFound } from "next/navigation";

/** Agents are not publicly callable — use team or workflow embeds. */
export default function EmbedAgentChatPage() {
  notFound();
}
