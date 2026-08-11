# Dental Clinic — Atlas Instructions

This folder is the **Atlas functional layer** for the Dental Clinic domain workspace: agent prompts, team prompts, and workflow runbooks. Paste or load these into Atlas Team Builder, Agent Builder, and Workflow Builder.

Product blueprint: `docs/dental-clinic-blueprint.md`.

## What this covers

| Area | In Instructions? | Notes |
|------|------------------|-------|
| Patient FAQ / aftercare chat | **Yes (prompts + KB)** | Attach clinic KB to Patient Concierge |
| Book / reschedule / cancel appointments | **Yes (prompts + tools)** | Front Desk + workflows; needs PMS connector |
| Recall / reminder outreach | **Yes (workflow)** | Recall reminder workflow + schedules |
| Staff scheduling copilot | **Yes (Front Desk team)** | Internal staff surface |
| Clinician chart / treatment summaries | **Yes (Clinician Copilot)** | Read-only explain; no diagnosis |
| Full PMS (chart UI, claims, imaging) | **No** | Open Dental or equivalent SoR via API gateway |
| eRx / ERA / tooth chart UI | **No** | Bridge to PMS; Atlas orchestrates |

**Bottom line:** Instructions ship the Atlas agents, teams, and workflows for a clinic tenant. Live appointment and chart data require a dental PMS API toolkit when you connect Open Dental or your SoR.

## Layout

```text
Instructions/DentalClinic/
  README.md                 # this file
  SKILL.md                  # index: personas, teams, workflows
  agents/                   # per-agent system prompts (.md)
  teams/                    # team orchestration prompts (.md)
  workflows/                # step contracts (.md)
```

## Auto-provision mapping (domain: `dental_clinic`)

When a super admin or org admin creates a tenant with domain **Dental Clinic**, Atlas auto-provisions:

| Kind | Slug | File |
|------|------|------|
| Agent | `front-desk` | `agents/front_desk.md` |
| Agent | `patient-concierge` | `agents/patient_concierge.md` |
| Agent | `clinician-copilot` | `agents/clinician_copilot.md` |
| Team | `front-desk-team` | `teams/front_desk_team.md` |
| Team | `patient-support` | `teams/patient_support.md` |
| Workflow | `book-appointment` | `workflows/book_appointment.md` |
| Workflow | `recall-reminder` | `workflows/recall_reminder.md` |

## How to load manually (optional)

1. Create agents from `agents/*.md` (instructions field).
2. Create teams from `teams/*.md`; attach member agents.
3. Create workflows from `workflows/*.md` as sequential steps.
4. Attach a dental PMS toolkit when available (Open Dental API gateway).
5. Publish agents → teams → workflows; assign teams to org users.

## Recommended PMS tool surface (future)

| Capability | Read / mutate | Used by |
|------------|---------------|---------|
| `search_patients` | read | Front Desk |
| `get_appointment_slots` | read | Front Desk, Concierge |
| `book_appointment` | mutate | Front Desk |
| `reschedule_appointment` | mutate | Front Desk |
| `cancel_appointment` | mutate | Front Desk |
| `list_due_recalls` | read | Front Desk |
| `get_treatment_plan_summary` | read | Clinician Copilot |
| `get_account_balance` | read | Front Desk (staff only) |

All mutating calls should use Atlas HITL approval when attached as tenant tools.
