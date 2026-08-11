# Dental Clinic — Skill Reference

Atlas skill pack for clinic patient engagement and front-office operations.

- **Domain id:** `dental_clinic`
- **Teams:** `teams/front_desk_team.md`, `teams/patient_support.md`
- **Agents:** see table below
- **Workflows:** book appointment, recall reminder
- **Blueprint:** `docs/dental-clinic-blueprint.md`
- **PMS policy:** Prefer Open Dental (or equivalent) as system of record; Atlas orchestrates via tenant-scoped API tools — do not rebuild full PMS in Atlas.

---

## Personas → Atlas agents

| Persona | Agent file | Primary scope | Mutating? |
|---------|------------|---------------|-----------|
| Front desk / scheduling | `agents/front_desk.md` | Book, reschedule, cancel, recalls, intake | yes (appointments) |
| Patient concierge | `agents/patient_concierge.md` | FAQ, aftercare, self-service chat | route only (no direct chart writes) |
| Clinician copilot | `agents/clinician_copilot.md` | Chart summaries, treatment plan explain | no (read/explain) |

Staff-facing agents require org membership. Patient-facing concierge runs on the public or verified patient portal.

---

## Teams

| Team | Slug | Members | Mission |
|------|------|---------|---------|
| Front Desk Team | `front-desk-team` | Front Desk | Scheduling, recalls, intake for staff |
| Patient Support | `patient-support` | Patient Concierge | Patient FAQ and appointment help |

---

## Workflows

| Workflow | Slug | File |
|----------|------|------|
| Book appointment | `book-appointment` | `workflows/book_appointment.md` |
| Recall reminder | `recall-reminder` | `workflows/recall_reminder.md` |

---

## Hard rules (every agent)

1. **Never diagnose** or prescribe — explain care instructions from approved KB or chart summaries only.
2. **Never invent** appointment times, balances, or insurance outcomes; use tools or say you cannot verify.
3. **PHI minimization** — confirm patient identity before discussing chart details on patient channels.
4. **Mutating appointment actions** require explicit patient or staff confirmation and HITL when tools are marked mutating.
5. **Escalate** clinical emergencies to human staff; do not attempt triage beyond scripted intake.
6. Prefer tool results over memory for schedule slots, recall due dates, and account status.
