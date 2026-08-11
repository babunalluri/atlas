# Workflow: Book Appointment

## Trigger

Staff or verified patient asks to schedule a new visit, hygiene recall, or consultation.

## Actors

- Front Desk (primary)
- Patient Concierge (optional intake on patient channel before handoff)

## Steps

1. **Identify patient** — search chart or create new patient intake record (name, DOB, phone, reason).
2. **Preferences** — provider, location, earliest acceptable date, time-of-day preference, duration needed.
3. **Availability** — `get_appointment_slots` for window; present 2–3 options.
4. **Confirm** — staff or verified patient selects slot; read back date, time, provider, location.
5. **Book** — `book_appointment` (HITL) with operatory and appointment type.
6. **Confirm output** — share confirmation number / summary; mention forms or arrival time policy from KB.

## Pass

- Appointment exists in PMS tool response with matching slot and patient id.
- Patient receives confirmation summary suitable for SMS/email template (staff may send).

## Fail

- Slot no longer available → re-query and offer alternatives.
- Unverified patient on public channel → stop before chart read; request identity verification.
- Tool error → do not claim booking succeeded.
