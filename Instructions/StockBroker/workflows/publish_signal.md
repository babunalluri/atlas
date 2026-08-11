# Workflow: Publish / Suppress Signal (UC-3)

## Trigger

Operator asks to publish a draft pack/signal or suppress a bad live signal.

## Actors

- Signal Publisher (primary)
- Param Editor (if schema/draft invalid)
- Feed Monitor (optional verify after publish)

## Steps

1. **Load draft** — `list_draft_signals` → pick `signal_id` / pack version.
2. **Validate content** — `get_signal`; confirm segment, entry/SL/targets, schema version.
3. **Param gate** — if keys invalid or unexpected, stop → Param Editor `update_param_draft` + `diff_param_versions`.
4. **Optional preview** — `preview_push(signal_id, device_ids?)`.
5. **Publish** — operator confirms → `publish_signal` (HITL).
6. **Verify** — Feed Monitor `get_feed_health` + customer-visible `list_signals` (entitled test user).
7. **Suppress path** — bad signal → `suppress_signal(reason)` → confirm absent from customer feed within session.

## Pass

- Entitled users see published signal; Free may get entitlement lock (not a publish bug).
- Suppressed signal hidden (F-025).
- Audit fields present on tool response when API provides them.

## Fail

- Draft visible to customers before publish.
- Publish without schema validation.
- Suppress not reflected in feed.
