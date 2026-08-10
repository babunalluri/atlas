# Dental clinic on Atlas — product blueprint

Atlas is the **multi-tenant agent / workflow / tools shell**.  
The dental **system of record** (patients, appointments, chart, billing) is a separate data plane Atlas tools call under strict tenant isolation.

This document is the build blueprint: what to build where, Open Dental feature gaps, and delivery order.

---

## 1. Fit summary

| Layer | Responsibility |
|--------|----------------|
| **Atlas** | Tenants, agents, teams, workflows, tools, knowledge, approvals, schedules, public/patient chat |
| **Dental system of record (SoR)** | Patients, charting, appointments, billing, insurance, imaging metadata — see §12 |
| **Not rebuilt in Atlas** | Full PMS parity with Open Dental (chart UI, clearinghouse, sensor capture, eRx networks) |

**Tenant model:** one Clerk org / Atlas tenant = one clinic (multi-location / DSO = later phase).

### Recommended product shape (after Open Dental review)

**Prefer hybrid:** **Open Dental (or equivalent PMS) = clinical/financial SoR**, **Atlas = AI + patient engagement + automation**.

Rebuilding every Open Dental module in Surreal is multi-year PMS work and weaker for claims/ledger integrity than a battle-tested stack. Use Surreal/Postgres **only** if the clinic refuses a PMS — then accept a **deliberately smaller** feature set (§2.A) and keep an OD migration path.

---

## 2. Requirements split

### A. Must-have clinic data & screens (system of record)

These need durable structured storage + staff UI (or PMS). Agents alone are not enough.

| Domain | Examples |
|--------|----------|
| **Patients / family** | Demographics, contacts, consent, medical alerts, guarantor / family links |
| **Appointments** | Book / reschedule / cancel, operatory/provider, status, recalls, confirmations |
| **Clinical** | Procedures (CDT), progress notes, treatment plans, perio summaries |
| **Account** | Fees, payments, statements, aging (light or via PMS) |
| **Insurance** | Plans, claims status (prefer PMS/clearinghouse; Atlas reads/explains) |
| **Documents / imaging metadata** | Categories, URIs; capture via PMS/bridges |
| **Staff access** | Front desk vs clinician vs billing vs owner roles |

### B. AI / agent features (Atlas-native)

| Feature | Atlas building blocks |
|---------|------------------------|
| Patient FAQ / aftercare | Knowledge + public or signed-in chat |
| Book / change appointment via chat | Tools + optional approvals |
| Plain-language treatment / claim status | Tools reading SoR |
| No-show / recall / eConfirm-style reminders | Schedules + notification tools |
| Intake triage / web forms → chart | Workflow |
| Staff “paused” sensitive actions | Approvals |
| Staff copilots (schedule, account Q&A) | Teams + tools against SoR |

### C. Out of scope for Atlas-native rebuild

Core Open Dental (or bridge) capabilities — **do not re-implement in Atlas MVP** unless product strategy explicitly chooses greenfield PMS:

- Graphical tooth chart / perio charting UI  
- Electronic claims, ERA, eligibility, EOBs, full aging A/R  
- Native sensor / intraoral capture and mounts  
- eRx / EPCS controlled substances  
- Lab case bridges  
- Full production/collection report suite  
- Full family accounts, guarantors, payment plans  

Atlas **orchestrates and explains** these via tools against the PMS/API.

---

## 3. Architecture

### 3A. Recommended — hybrid (Open Dental + Atlas)

```text
┌──────────────────────────────────────────────────────────────┐
│ Atlas                                                        │
│  Agents · Teams · Workflows · Knowledge · Approvals · Chat   │
└────────────────────────────┬─────────────────────────────────┘
                             │ Dental API tools (tenant-scoped)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ Dental API gateway (Atlas backend)                           │
│  Maps tenant_id → OD clinic connection / API credentials     │
│  Never accepts cross-tenant connection strings from the LLM  │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ Open Dental (per clinic or DSO DB + clinic filter)           │
│  Appointments · Chart · Account · Imaging · Manage · eServices│
└──────────────────────────────────────────────────────────────┘
```

**Isolation:** each Atlas tenant has its own OD database **or** OD clinic key; gateway loads credentials only for `TenantContext.tenant_id`.

### 3B. Alternate — greenfield Surreal (limited PMS)

```text
Atlas tools → Dental API → Surreal namespace per tenant
```

Use only for MVP domains in §2.A; plan OD migration if insurance/imaging become required.

### Isolation (mandatory in both models)

1. Connection / namespace / OD clinic resolved **only** from `TenantContext.tenant_id`  
2. Tool args never accept raw DB URL / NS / password / clinic id override  
3. Profile IDs and patient PatNums scoped to that tenant’s SoR  
4. Org A agent cannot load Org B patients (404 / empty under tenant bind)

---

## 4. Data model

### 4.1 If hybrid (Open Dental is SoR)

Do **not** duplicate OD ledgers in Surreal. Store in Atlas only:

| Atlas-side | Purpose |
|------------|---------|
| `dental_connection` | tenant_id, OD API/base URL or site id, credential_id, clinic filter |
| Optional sync cache | Read-through cache of patient/appt summaries (TTL); OD remains authoritative |
| Knowledge / comms templates | Non-PHI aftercare and scripts |

Map tools to OD entities: Patient, Appointment, ProcedureLog, TreatPlan, Claim, Payment, Document, Commlog, etc.

### 4.2 If greenfield Surreal (MVP subset)

Prefer **one Surreal namespace per Atlas tenant** (e.g. `clinic_<tenant_uuid>`).

| Record | Key fields |
|--------|------------|
| `patient` | id, external_id, name, phone, email, dob, alerts[], allergies[], meds[], consent, family_id, created_at |
| `appointment` | id, patient_id, provider_id, operatory, starts_at, ends_at, status, type, reason |
| `provider` | id, name, npi, role, schedule_defaults, active |
| `operatory` | id, name, active |
| `procedure` | id, patient_id, code (CDT), tooth, surfaces, status, fee, date |
| `treatment_plan` | id, patient_id, status, items[] (procedure refs) |
| `visit_note` | id, patient_id, appointment_id, summary, created_by, created_at |
| `perio_exam` | id, patient_id, date, pockets/summary blob or structured sites |
| `insurance_plan` | id, carrier, subscriber, patient_id, priority (primary/secondary) |
| `claim` | id, patient_id, status, amount, submitted_at *(later)* |
| `payment` | id, patient_id, amount, method, posted_at |
| `invoice` / `statement` | id, patient_id, balance, lines[], status |
| `document` | id, patient_id, category, uri (object store), mime, created_at |
| `commlog` | id, patient_id, channel, direction, body_ref, created_at |
| `recall` | id, patient_id, type, due_date, status |
| `lab_case` | id, patient_id, lab, status, due_date *(later)* |
| `rx` | id, patient_id, drug, sig, status *(prefer eRx bridge, not home-grown)* |

---

## 5. Atlas customizations

### 5.1 Tools (dental toolkit)

Implement as tenant tools (HTTP to an internal dental API **or** OD gateway). Recommended: **thin Dental API** in Atlas backend that owns SoR access (audit + isolation).

#### Staff tools

| Tool | Purpose | Mutating? |
|------|---------|-----------|
| `dental_search_patients` | Find by name/phone/email | No |
| `dental_get_patient` | Demographics + alerts + family summary | No |
| `dental_list_appointments` | Range + patient / provider / operatory filter | No |
| `dental_book_appointment` | Create booking | Yes → approval optional |
| `dental_reschedule_appointment` | Move booking | Yes |
| `dental_cancel_appointment` | Cancel | Yes → approval |
| `dental_get_treatment_summary` | Plan + recent notes / procedures | No |
| `dental_create_visit_summary` | Write plain-language note | Yes → approval |
| `dental_list_recalls_due` | Patients needing recall | No |
| `dental_get_account_summary` | Balance, recent payments, open statements | No |
| `dental_get_claim_status` | Claim / benefit snapshot from SoR | No |
| `dental_list_documents` | Document categories / titles (not binary dump) | No |
| `dental_log_comm` | Append commlog / outbound message metadata | Yes → approval if PHI |

**Staff-only.** Never attach these to anonymous / public patient chat.

#### Patient self-service tools (bound identity)

| Tool | Purpose | Mutating? |
|------|---------|-----------|
| `dental_my_profile` | Demographics the patient is allowed to see | No |
| `dental_my_appointments` | Upcoming / recent for **this** patient | No |
| `dental_my_treatment_summary` | Plain-language plan + notes for **this** patient | No |
| `dental_my_account_summary` | Balance / last payment (no full ledger dump) | No |
| `dental_my_book_appointment` | Request or book a slot for **this** patient | Yes → optional approval |
| `dental_my_reschedule_appointment` | Move **own** appointment only | Yes |
| `dental_my_cancel_appointment` | Cancel **own** appointment only | Yes → optional approval |

Backend ignores any `patient_id` from the model; it always uses `session.patient_id`.

Agents call **tool names only**; backend binds SoR under current tenant.

### 5.2 Agents (starter set)

| Agent | Job |
|-------|-----|
| **Front desk** | FAQ, book/reschedule, hours, directions (**staff** tools) |
| **Clinical assistant** | Treatment explanations, aftercare (no silent chart writes) |
| **Billing assistant** | Account/claim status explanations; never submit claims without approval |
| **Ops / recall** | Lists due recalls, drafts reminder messages |
| **Patient portal assistant** | Verified patients only: my appointments, my summary, aftercare |

### 5.3 Teams

| Team | Members |
|------|---------|
| **Clinic frontline** | Front desk + clinical assistant (route/coordinate) — **staff** |
| **Clinic billing desk** | Billing assistant (+ front desk optional) — **staff** |
| **Patient self-service** | Patient portal assistant only — **no staff search tools** |

### 5.4 Workflows

| Workflow | Steps |
|----------|--------|
| **New patient intake** | Collect info → create/update patient in SoR → book → send confirmation |
| **Post-visit summary** | Load plan/notes → draft summary → staff approve → send to patient |
| **No-show follow-up** | Detect no-show → draft outreach → approve → schedule retry |
| **Recall outreach** | List due recalls → draft Web Sched–style link/message → approve → send |
| **Patient verify & link** | OTP / magic link → bind `patient_id` to chat session → open self-service |
| **Claim status brief** | Load claim snapshot → plain-language brief for staff/patient (read-only) |

### 5.5 Knowledge (not the chart)

- Clinic hours, policies, parking  
- Consent / privacy blurbs (non-PHI templates)  
- Aftercare PDFs (wisdom teeth, scaling, whitening)  
- Insurance FAQ (general, not live eligibility)

### 5.6 Schedules

- Daily “tomorrow’s appointments” digest for staff  
- Weekly recall list run  
- Reminder / eConfirm-style messages N hours before appointment  
- Optional: broken-appointment / no-show sweep

### 5.7 Public / patient surfaces

- Hosted chat: `/t/{clinic}/teams/frontline` or workflow intake  
- Embed widget on clinic website  
- Never expose other clinics’ namespaces / OD connections

### 5.8 Patient self-service (fetch own info)

Patients **can** fetch their own info through Atlas chat/workflows once identity is bound. Anonymous guests get **knowledge-only** answers (hours, aftercare templates) — not charts or other patients’ data.

#### Identity bind (required before PHI tools)

1. Patient opens clinic chat / portal (`/t/{clinicSlug}/…` or embed).  
2. Verify via one of:  
   - Signed-in clinic patient account (Clerk or clinic auth), or  
   - One-time code to phone/email already on the patient record.  
3. On success, session stores `{ tenant_id, patient_id }` (server-side; not model-controlled).  
4. Only then attach the **Patient self-service** team/tools.

#### What the patient can fetch

| Ask | Source |
|-----|--------|
| “When is my next cleaning?” | `dental_my_appointments` |
| “What treatment did we discuss?” | `dental_my_treatment_summary` |
| “What do I owe?” | `dental_my_account_summary` |
| “Update / confirm my contact?” | `dental_my_profile` (+ optional update tool later) |
| “How do I care for my gums after scaling?” | Knowledge (no chart needed) |

#### Hard rules

| Rule | Why |
|------|-----|
| No `dental_search_patients` on patient surfaces | Prevents listing other patients |
| Tool layer forces `patient_id = session.patient_id` | Model cannot pass another id |
| Same Atlas tenant isolation as staff | Clinic A never hits Clinic B SoR |
| Unverified session → knowledge tools only | Avoid PHI leakage |
| Audit patient reads of appointments/summaries | Integrity / compliance |

#### UX sketch

```text
Patient chat (unverified)
  → hours, parking, general aftercare (Knowledge)

Patient verifies OTP / login
  → session.patient_id bound
  → “Show my appointments” / “Summarize my treatment”
  → dental_my_* tools only
```

Staff continue using **Clinic frontline** with full `dental_*` staff tools in the admin / staff workspace — never the reverse.

---

## 6. Staff UI (beyond Atlas admin)

Atlas admin configures agents/tools. Clinics also need **operational screens**.

| Screen | Priority | Hybrid note |
|--------|----------|-------------|
| Patient list / detail | P0 | Prefer OD UI; Clinic UI only if greenfield |
| Calendar / appointments | P0 | Same |
| Treatment plan / chart | P0 | OD Chart; Atlas does not replace graphical chart |
| Today’s board | P1 | OD or thin Clinic board |
| Account / statements | P1 | OD Account; Atlas billing assistant for Q&A |
| Imaging | P1 | OD Imaging + bridges; Atlas lists metadata only |

Options:

1. **Integrate Open Dental** and build Atlas tools against its API / middleware (**recommended for full clinic**)  
2. **Build light Next.js “Clinic” app** (`apps/clinic`) hitting Dental API — only for greenfield MVP  

MVP recommendation: **hybrid OD + Atlas AI**; greenfield Surreal + minimal Clinic UI only if no PMS.

---

## 7. Compliance & integrity checklist

- Encrypt SoR / OD credentials; rotate via Credentials  
- Audit every mutating tool (who, patient id, action)  
- Minimize PHI in traces / logs (redact phone/email where possible)  
- Retention policy per tenant  
- BAAs / hosting region as required by jurisdiction  
- Approvals on cancel, outbound patient messages with PHI, chart writes, claim submissions  
- Patient portal / chat: identity bind before any PHI tool  

---

## 8. Delivery phases

### Phase 0 — Blueprint accepted (this doc)

### Phase 1 — SoR path + isolation

**Hybrid:** `dental_connection` + OD gateway; patients + appointments read API; tenant bind tests.  
**Greenfield:** Surreal namespace per tenant; patients + appointments CRUD; seed demo clinic.

### Phase 2 — Atlas dental tools + agents

- Read tools first, then mutating with approvals  
- Front desk agent + frontline team  
- Knowledge pack for aftercare  
- **Patient verify workflow** + `dental_my_*` tools + patient self-service team  

### Phase 3 — Workflows + schedules (eServices parity in Atlas)

- Intake + post-visit + reminders / eConfirm-style  
- Recall outreach  
- Patient self-service booking/reschedule with optional approval  

### Phase 4 — Account / clinical depth (via SoR)

- Account summary + claim status tools  
- Treatment plan / procedure reads  
- Billing assistant team  
- Clinic UI only if greenfield; otherwise deepen OD integration  

### Phase 5 — Specialist bridges (do not rebuild)

- Imaging metadata / document list tools  
- eRx / lab case status via vendor bridges  
- Payments provider; SMS/email  
- Multi-location / DSO clinic filter  

---

## 9. Success criteria (MVP)

1. Two Atlas tenants; Clinic A tools never return Clinic B patients  
2. Staff can book/list appointments via SoR UI **and** via front-desk agent tools  
3. Patient chat answers aftercare from knowledge without leaking other clinics  
4. **Verified patient** can fetch only their appointments / treatment summary via `dental_my_*`  
5. Unverified patient cannot call staff search tools or another patient’s data  
6. Mutating actions can pause for approval  
7. Treatment summary tool returns structured + plain-language output for one patient  
8. (Hybrid) Account/claim **read** tools return tenant-scoped OD data without dual ledgers  

---

## 10. Decision log

| Decision | Choice |
|----------|--------|
| Agent platform | Atlas (existing) |
| Preferred SoR for full clinic | **Open Dental (or equivalent PMS) via Dental API gateway** |
| Greenfield store (optional MVP) | SurrealDB namespace per tenant; Postgres acceptable if Surreal ops cost is high |
| Connection model | Server-side Dental API; no free-form DB URLs in tools |
| Product shape | Atlas AI shell + PMS SoR; light Clinic UI only if no PMS |
| PMS replacement | **Not the goal**; Atlas complements OD; greenfield only for limited MVP |

---

## 11. Next implementation slice

When ready to code, start with **Phase 1**:

**Hybrid path (preferred):**

1. `dental_connection` + encrypted OD credentials per tenant  
2. Internal `/api/dental/...` gateway: search patients, list appointments  
3. Isolation tests: cross-tenant OD credentials never load  

**Greenfield path:**

1. Surreal schema + tenant namespace provisioning on tenant create  
2. Internal dental routes under tenant bind  
3. First two tools: `dental_search_patients`, `dental_list_appointments`  
4. Isolation tests: cross-tenant reads return empty/404  

Then wire a Front desk agent in the Atlas UI against those tools.

---

## 12. Open Dental feature gap analysis

Inventory of major Open Dental modules vs this blueprint, with the **right solution** for each gap.

Legend:

| Tag | Meaning |
|-----|---------|
| **Covered** | In §2–§5 as Atlas + SoR MVP |
| **Partial** | Some coverage; extend tools/workflows |
| **Gap** | Missing vs OD; solution specified |
| **Integrate** | Keep in OD / vendor; Atlas does not rebuild |
| **Atlas-replace** | OD eService that Atlas can own better (AI + chat) |

### 12.1 Appointments module

| OD capability | Status | Solution |
|---------------|--------|----------|
| Operatory / provider schedule | Partial | SoR stores provider + operatory; hybrid → OD Appts module remains UI of record |
| Book / edit / break / complete | Covered | Tools + Clinic or OD UI |
| Pinboard / planned appointments | Gap | Hybrid: OD planned appts; greenfield: `appointment.type=planned` + tool `dental_list_planned` |
| Recall system | Partial | `recall` record + `dental_list_recalls_due` + schedule workflow; OD Recall if hybrid |
| Production by operatory | Gap | Integrate OD reports; optional later analytics read API — not Atlas MVP |
| Appt alerts / hover notes | Gap | Surface `patient.alerts` + family urgent notes in `dental_get_patient`; UI alerts stay in OD |
| Family recall board | Gap | Family-aware recall list tool when `family_id` / OD family exists |

### 12.2 Family module

| OD capability | Status | Solution |
|---------------|--------|----------|
| Family / guarantor links | Gap → Partial | Add `family_id` / guarantor on patient; hybrid use OD Family |
| Urgent financial notes | Gap | Field on patient/family; show in get-patient + appt tools |
| Super family / referrals | Integrate | OD Manage; Atlas tools optional later |

### 12.3 Account module

| OD capability | Status | Solution |
|---------------|--------|----------|
| Procedure fees / adjustments | Gap | **Integrate OD Account**; greenfield only light `invoice`/`payment` |
| Insurance plans / subscribers | Gap | Hybrid OD; greenfield `insurance_plan` metadata only |
| Claims / ERA / EOBs | Integrate | Clearinghouse via OD; Atlas `dental_get_claim_status` read-only + billing agent |
| Statements / aging | Gap | OD statements; Atlas `dental_get_account_summary` + `dental_my_account_summary` |
| Payment plans / splits | Integrate | OD; do not rebuild |
| Commlog | Partial | `commlog` + `dental_log_comm`; hybrid write through OD Commlog |

### 12.4 Treatment Plan module

| OD capability | Status | Solution |
|---------------|--------|----------|
| Multi-visit plans, present to patient | Covered / Partial | `treatment_plan` + summary tools; graphical present → OD or Clinic UI |
| Saved TP versions | Gap | Prefer OD TreatPlan history; greenfield version field later |

### 12.5 Chart module

| OD capability | Status | Solution |
|---------------|--------|----------|
| Graphical tooth chart | Integrate | Never rebuild in Atlas; OD Chart UI |
| Progress notes / procedure log | Partial | `procedure` + `visit_note`; agents summarize, staff chart in OD |
| Perio charting | Gap | Integrate OD perio; optional `perio_exam` summary for AI read |
| Chart views / object types | Integrate | OD UI |
| Planned appt from chart | Gap | OD; greenfield link treatment items → planned appt |

### 12.6 Imaging module

| OD capability | Status | Solution |
|---------------|--------|----------|
| Sensor / camera capture | Integrate | OD Imaging + device bridges |
| Image categories / mounts | Integrate | OD |
| Patient portal image folders | Partial | `dental_list_documents` / my-docs later; binaries via signed URLs from SoR |
| Import PDF / scans | Partial | Object store + `document` metadata; capture in OD if hybrid |

### 12.7 Manage module

| OD capability | Status | Solution |
|---------------|--------|----------|
| Providers, operatories, schedules | Partial | `provider` / `operatory` tables or OD setup |
| Fee schedules | Gap | Integrate OD fee schedules; greenfield fee table phase 4+ |
| Security / users | Partial | Atlas + Clerk roles for AI; OD security for PMS users |
| Tasks / messaging | Gap | Atlas Approvals + workflows for AI tasks; OD Tasks for clinic ops or later sync |
| Reports / queries | Integrate | OD reports; Atlas may draft narratives from tool outputs |
| Sheets / forms design | Partial | Atlas workflows + web forms; OD Sheets if hybrid |
| Supply / inventory | Integrate | Out of MVP |
| Multi-clinic / DSO | Gap | Phase 5 clinic filter on connection |

### 12.8 eServices (Open Dental)

| OD eService | Status | Solution |
|-------------|--------|----------|
| Patient Portal | Atlas-replace / Partial | Atlas patient verify + `dental_my_*` (§5.8); hybrid can coexist with OD portal |
| Web Sched New Patient / Recall | Atlas-replace / Partial | Intake + recall workflows + public booking tools |
| eConfirmations / eReminders | Atlas-replace | Schedules + SMS/email tools (§5.6) |
| Integrated Texting | Partial | Notification tool + `commlog`; vendor SMS |
| Web Forms / eClipboard | Partial | Atlas workflows + mobile-friendly forms; OD eClipboard if already paid |
| eRx | Integrate | DoseSpot/etc. via OD; Atlas never home-grows EPCS |
| Mobile Web | Integrate | OD mobile; Atlas chat is separate surface |

### 12.9 Labs, Rx, misc clinical

| OD capability | Status | Solution |
|---------------|--------|----------|
| Lab cases | Integrate | Bridge status tool later (`dental_get_lab_case`) |
| Prescriptions | Integrate | eRx network; read-only med list on patient for AI context |
| Medical history / allergies | Partial | Fields on `patient`; forms workflow; OD med hx if hybrid |

### 12.10 Gap → solution matrix (priority)

| Priority | Gap | Right solution |
|----------|-----|----------------|
| P0 | Full insurance / claims / ERA | **Do not build** — Open Dental Account + clearinghouse; Atlas read/explain tools |
| P0 | Graphical chart / perio UI | **Do not build** — OD Chart; AI summaries only |
| P0 | Imaging capture | **Do not build** — OD Imaging + sensors |
| P0 | Tenant-safe SoR | Hybrid OD connection **or** Surreal NS per tenant |
| P1 | Family / guarantor | Model + tools; or OD Family |
| P1 | Account summary for staff & patient | `dental_get_account_summary` / `dental_my_account_summary` |
| P1 | Recall + confirmations | Schedules + workflows (Atlas eServices parity) |
| P1 | Commlog | Tool + SoR write |
| P2 | Fee schedules / production reports | OD reports / fee tables |
| P2 | Documents list for AI | Metadata tools + object storage |
| P2 | Lab / eRx status | Vendor bridges |
| P2 | Multi-location DSO | Clinic filter on `dental_connection` |
| P3 | Inventory, supply, advanced Manage | Stay in OD / skip |

### 12.11 Strategic recommendation

1. **Default:** Open Dental as SoR + Atlas as AI / patient engagement / automation layer (§3A).  
2. **If building Surreal greenfield:** ship §2.A only; treat §12 Integrate rows as **explicit non-goals** until OD (or another PMS) is connected.  
3. **Win where Atlas beats OD eServices:** conversational portal, verified `dental_my_*`, approval-gated outreach, knowledge aftercare, multi-tenant SaaS packaging.  
4. **Never chase OD parity** inside Atlas — chase **integrity + isolation + AI leverage** on top of a real PMS.
