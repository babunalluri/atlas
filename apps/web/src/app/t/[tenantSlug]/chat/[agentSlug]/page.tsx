import { notFound } from "next/navigation";

/** Agents are not publicly callable — use team or workflow hosted chat. */
export default function CustomerChatPage() {
  notFound();
}
