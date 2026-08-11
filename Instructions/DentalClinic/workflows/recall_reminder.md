# Workflow: Recall Reminder

## Trigger

Staff runs weekly recall review, or scheduled job kicks off outreach prep.

## Actors

- Front Desk (primary)

## Steps

1. **Define window** — e.g. recalls due in next 14 days; filter by provider or location if requested.
2. **List due** — `list_due_recalls` (or PMS equivalent); capture patient id, last visit, recall type, contact preference.
3. **Prioritize** — overdue > due this week; flag patients without valid phone/email for manual follow-up.
4. **Draft outreach** — for each batch, generate short reminder text from clinic template (KB); **do not send** until staff approves if using mutating notify tools.
5. **Optional book** — for patients who respond, branch to **Book appointment** workflow with prefilled recall type.
6. **Log** — summarize count contacted, skipped, and manual follow-ups needed.

## Pass

- Due list matches tool output counts.
- No outreach sent without staff approval when using mutating notification tools.
- Overdue patients clearly flagged for phone follow-up when digital contact missing.

## Fail

- Invented recall dates or patient lists.
- Bulk send without approval on mutating channels.
- Clinical advice included in reminder copy — reminders are scheduling only.
