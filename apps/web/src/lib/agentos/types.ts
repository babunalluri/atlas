/** Typed AgentOS run stream events. */

export type RunEventName =
  | "RunStarted"
  | "RunContent"
  | "RunContentCompleted"
  | "RunIntermediateContent"
  | "RunCompleted"
  | "RunError"
  | "RunCancelled"
  | "RunPaused"
  | "RunContinued"
  | "ToolCallStarted"
  | "ToolCallCompleted"
  | "ToolCallError"
  | "MemoryUpdateStarted"
  | "MemoryUpdateCompleted"
  | string;

export interface RunEventBase {
  event: RunEventName;
  run_id?: string;
  session_id?: string;
  agent_id?: string;
  created_at?: number;
  content?: unknown;
  content_type?: string;
  error?: string | null;
  tools?: unknown;
  tool?: unknown;
  requirements?: Array<Record<string, unknown>>;
  approval_ids?: string[];
  references?: unknown;
  citations?: unknown;
  model?: string;
  model_provider?: string;
  [key: string]: unknown;
}

export interface ParsedSseFrame {
  id?: string;
  event?: string;
  data: string;
  retry?: number;
}

export interface StreamRunOptions {
  url: string;
  accessToken: string;
  body: Record<string, unknown> | FormData;
  signal?: AbortSignal;
  lastEventId?: string;
  onEvent: (event: RunEventBase, frame: ParsedSseFrame) => void;
  onRawFrame?: (frame: ParsedSseFrame) => void;
}

/** Guest / anonymous public chat stream (no Clerk token). */
export interface StreamPublicRunOptions {
  url: string;
  guestId: string;
  body: FormData;
  signal?: AbortSignal;
  lastEventId?: string;
  onEvent: (event: RunEventBase, frame: ParsedSseFrame) => void;
  onRawFrame?: (frame: ParsedSseFrame) => void;
}

export class SseError extends Error {
  readonly status?: number;
  readonly bodyText?: string;

  constructor(message: string, status?: number, bodyText?: string) {
    super(message);
    this.name = "SseError";
    this.status = status;
    this.bodyText = bodyText;
  }
}
