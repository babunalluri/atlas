"use client";

import { useRouter } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Textarea } from "@/components/ui/Field";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import {
  CheckIcon,
  CloseIcon,
  EyeIcon,
  EyeOffIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  SaveIcon,
  TrashIcon,
} from "@/components/ui/icons";
import {
  type AgentSchedule,
  createSchedule,
  deleteSchedule,
  runScheduleNow,
  type ScheduleTarget,
  setScheduleEnabled,
  updateSchedule,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

const TIMEZONES = [
  "UTC",
  "America/Los_Angeles",
  "America/Chicago",
  "America/New_York",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

function formatNextRun(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

export function ScheduleManager({
  schedules,
  targets,
}: {
  schedules: AgentSchedule[];
  targets: ScheduleTarget[];
}) {
  const router = useRouter();
  const tCommon = useTranslations("common");
  const { getAccessToken } = useAgentOsToken();
  const runnableTargets = targets.filter(
    (target) =>
      target.target_type === "team" || target.target_type === "workflow",
  );
  const [editing, setEditing] = useState<AgentSchedule | null>(null);
  const [name, setName] = useState("");
  const [cron, setCron] = useState("0 9 * * 1-5");
  const [timezone, setTimezone] = useState("UTC");
  const [message, setMessage] = useState("");
  const [targetIndex, setTargetIndex] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setEditing(null);
    setName("");
    setCron("0 9 * * 1-5");
    setTimezone("UTC");
    setMessage("");
    setTargetIndex(0);
    setError(null);
  }

  function edit(schedule: AgentSchedule) {
    const index = runnableTargets.findIndex(
      (target) =>
        target.target_type === schedule.target_type &&
        target.version_id === schedule.version_id,
    );
    setEditing(schedule);
    setName(schedule.name);
    setCron(schedule.cron_expression);
    setTimezone(schedule.timezone);
    setMessage(schedule.message);
    if (index === -1) {
      setTargetIndex(-1);
      setError("Pinned target is missing from published targets.");
    } else {
      setTargetIndex(index);
      setError(null);
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function save() {
    if (targetIndex < 0) {
      setError("Pinned target is missing from published targets.");
      return;
    }
    const target = runnableTargets[targetIndex];
    if (!target || !name.trim() || !cron.trim() || !message.trim()) {
      setError("Name, target, cron, and message are required.");
      return;
    }
    setBusy("save");
    setError(null);
    try {
      const token = await getAccessToken();
      const payload = {
        name: name.trim(),
        cron_expression: cron.trim(),
        timezone,
        enabled: editing?.enabled ?? true,
        target_type: target.target_type,
        target_id: target.target_id,
        version_id: target.version_id,
        message: message.trim(),
        input_payload: editing?.input_payload ?? {},
      };
      if (editing) {
        await updateSchedule(token, editing.id, payload);
      } else {
        await createSchedule(token, payload);
      }
      reset();
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save schedule");
    } finally {
      setBusy(null);
    }
  }

  async function act(id: string, action: () => Promise<unknown>) {
    setBusy(id);
    setError(null);
    try {
      await action();
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Schedule action failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <section className="surface-panel relative overflow-hidden rounded-2xl p-6 md:p-8">
        <div className="pointer-events-none absolute inset-0 grid-noise opacity-60" />
        <div className="relative grid gap-7 lg:grid-cols-[1fr_1.1fr]">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
              Atlas automation
            </p>
            <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">
              Put reliable agent work on a clock.
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-muted">
              Run a pinned, published team or workflow on a tenant-isolated cron
              schedule. Agents belong inside teams and workflows — they are not
              scheduled directly. Every attempt creates a durable session and run
              record.
            </p>
          </div>
          <div className="grid gap-3 rounded-xl border border-line bg-raised/90 p-4">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold">
                {editing ? "Edit schedule" : "New schedule"}
              </p>
              {editing ? (
                <Button size="sm" variant="ghost" icon={<CloseIcon />} onClick={reset}>
                  {tCommon("cancel")}
                </Button>
              ) : null}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <Label htmlFor="schedule-name">Name</Label>
                <Input
                  id="schedule-name"
                  value={name}
                  placeholder="Weekday support digest"
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="schedule-target">Published target</Label>
                <SearchableSelect
                  id="schedule-target"
                  value={String(targetIndex)}
                  onChange={(value) => setTargetIndex(Number(value))}
                  placeholder="Select target"
                  options={[
                    ...(targetIndex < 0
                      ? [
                          {
                            value: "-1",
                            label: "Pinned target missing",
                            disabled: true,
                          },
                        ]
                      : []),
                    ...runnableTargets.map((target, index) => ({
                      value: String(index),
                      label: `${target.name} · ${target.target_type}`,
                    })),
                  ]}
                />
              </div>
              <div>
                <Label htmlFor="schedule-cron" hint="5-field cron">
                  Cron expression
                </Label>
                <Input
                  id="schedule-cron"
                  className="font-mono"
                  value={cron}
                  onChange={(event) => setCron(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="schedule-timezone">Timezone</Label>
                <SearchableSelect
                  id="schedule-timezone"
                  value={timezone}
                  onChange={setTimezone}
                  options={TIMEZONES.map((zone) => ({
                    value: zone,
                    label: zone,
                  }))}
                />
              </div>
            </div>
            <div>
              <Label htmlFor="schedule-message">Run message</Label>
              <Textarea
                id="schedule-message"
                className="min-h-20"
                value={message}
                placeholder="Summarize unresolved support requests and recommend owners."
                onChange={(event) => setMessage(event.target.value)}
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-rose">{error}</p>
              <Button
                variant="accent"
                icon={<SaveIcon />}
                disabled={busy === "save" || targets.length === 0}
                onClick={save}
              >
                {busy === "save" ? "Saving…" : editing ? "Save changes" : "Create schedule"}
              </Button>
            </div>
          </div>
        </div>
      </section>

      <section className="table-shell overflow-x-auto rounded-xl">
        <div className="grid min-w-[900px] grid-cols-[1.4fr_.7fr_.8fr_.8fr_1.2fr] gap-3 border-b border-line px-4 py-2.5">
          <span className="th-label">Schedule</span>
          <span className="th-label">State</span>
          <span className="th-label">Last run</span>
          <span className="th-label">Next run</span>
          <span className="th-label text-right">Actions</span>
        </div>
        {schedules.map((schedule) => (
          <div
            key={schedule.id}
            className="grid min-w-[900px] grid-cols-[1.4fr_.7fr_.8fr_.8fr_1.2fr] items-center gap-3 border-b border-line/60 px-4 py-3 last:border-0"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{schedule.name}</p>
              <p className="mono-cell truncate text-slate-muted">
                {schedule.cron_expression} · {schedule.timezone} · {schedule.target_type}
              </p>
              {schedule.last_error ? (
                <p className="mt-1 truncate text-xs text-rose">{schedule.last_error}</p>
              ) : null}
            </div>
            <Badge tone={schedule.enabled ? "success" : "neutral"} dot>
              {schedule.enabled ? "enabled" : "disabled"}
            </Badge>
            <div>
              <Badge
                tone={
                  schedule.last_status === "completed"
                    ? "success"
                    : schedule.last_status === "error"
                      ? "danger"
                      : "neutral"
                }
              >
                {schedule.last_status ?? "not run"}
              </Badge>
              <p className="mono-cell mt-1 text-slate-muted">
                {schedule.last_run_at ? formatRelative(schedule.last_run_at) : "—"}
              </p>
            </div>
            <p className="mono-cell text-slate-muted">
              {schedule.next_run_at ? formatNextRun(schedule.next_run_at) : "—"}
            </p>
            <div className="flex justify-end gap-1.5">
              <Button size="sm" variant="ghost" icon={<PencilIcon />} onClick={() => edit(schedule)}>
                Edit
              </Button>
              <Button
                size="sm"
                variant="secondary"
                icon={schedule.enabled ? <EyeOffIcon /> : <EyeIcon />}
                disabled={busy === schedule.id}
                onClick={() =>
                  act(schedule.id, async () =>
                    setScheduleEnabled(
                      await getAccessToken(),
                      schedule.id,
                      !schedule.enabled,
                    ),
                  )
                }
              >
                {schedule.enabled ? "Disable" : "Enable"}
              </Button>
              <Button
                size="sm"
                variant="accent"
                icon={<PlayIcon />}
                disabled={busy === schedule.id}
                onClick={() =>
                  act(schedule.id, async () =>
                    runScheduleNow(await getAccessToken(), schedule.id),
                  )
                }
              >
                Run now
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<TrashIcon />}
                disabled={busy === schedule.id}
                onClick={() =>
                  act(schedule.id, async () =>
                    deleteSchedule(await getAccessToken(), schedule.id),
                  )
                }
              >
                Delete
              </Button>
            </div>
          </div>
        ))}
        {schedules.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-slate-muted">
            No schedules yet. Create one for a published target.
          </p>
        ) : null}
      </section>
    </div>
  );
}
