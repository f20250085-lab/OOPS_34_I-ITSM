# I-ITSM — Intelligent IT Service Management (BITS Pilani, Hyderabad Campus)

A runnable academic prototype for the CS F213 / MAC F212 OOP Semester-I 2026–27 project. Built with Python, Flask, SQLite, Jinja templates and object-oriented service classes. It uses no paid API or external AI service.

## Run locally

**Windows:** double-click `run.bat` or run it from PowerShell.  
**macOS/Linux:** `chmod +x run.sh && ./run.sh`.

Then open **http://127.0.0.1:5000**.

Requires Python 3.10+. The launcher creates a virtual environment and installs Flask. The SQLite database `itsm.db` is created and seeded automatically on first launch.

### Demo logins

| Role | Email | Password |
|---|---|---|
| Administrator | bitsadmin@itsm.demo | BITSadmin123 |
| Employee | bitsemployee@itsm.demo | BITSuser123 |
| Technician | bitsneha@itsm.demo | BITStech123 |
| Technician | bitsrohan@itsm.demo | BITStech123 |
| Technician | bitsisha@itsm.demo | BITStech123 |

Change demo passwords and `SECRET_KEY` before any deployment. This is an academic prototype, not a production-ready service.

## Implemented features

**Users and security**
- Three roles: Employee, Technician and Administrator. Employees can self-register; admins create technicians/admins and can deactivate accounts.
- Password hashing, session authentication, role-based route authorization, input validation, parameterized SQL, friendly 403/404 pages.

**Incident lifecycle**
- Create, view, update, assign, acknowledge, investigate, resolve, verify and close.
- State machine `NEW → CLASSIFIED → ASSIGNED → ACKNOWLEDGED → IN_PROGRESS → RESOLVED → VERIFIED → CLOSED` (reopen from RESOLVED). Illegal jumps raise `InvalidTransitionError`.
- Role rules: technicians cannot verify/close; the reporter can confirm the fix (VERIFIED) or reopen it.
- Activity thread: technician investigation notes (internal), "request information" to the reporter, reporter replies/additional information.
- Feedback: the reporter rates the support (1–5 stars) after resolution.

**Priority, assignment and SLA**
- Priority strategies (admin-selectable): Weighted `impact × urgency × criticality + min(users,100)/25` (P1 ≥ 20, P2 ≥ 12, P3 ≥ 6, else P4) and Conservative (additive).
- Three assignment strategies (admin-selectable default): Skill + workload, Least loaded, Round robin. Technicians at the workload limit (5 active) are skipped; if nobody is free a `TechnicianUnavailableError` is shown.
- SLA policies stored in the database and editable in **Settings** (defaults P1 5m/30m, P2 15m/2h, P3 1h/8h, P4 4h/24h).
- SLA monitor (runs automatically every 30 s and from the "Run SLA monitor" button): warns before deadlines (AT RISK), records response/resolution violations, tracks time to acknowledge and resolution time.

**Intelligent features**
1. Automatic category suggestion from the description (keyword scoring).
2. Automatic priority calculation.
3. Duplicate detection (Jaccard similarity ≥ 0.28 on title/description words).
4. Incident correlation: 3+ open incidents on the same service within 60 minutes → "possible common cause" alert.
5. Automatic escalation: unacknowledged incidents escalate to Team Lead (L1, after the response target) and IT Manager (L2, after 3× the response target).
6. Suggested resolutions from similar resolved incidents.
7. Smart technician recommendation (skills, workload, experience).

**Notifications, audit, search and reports**
- Two notification channels behind a `NotificationChannel` interface: in-app inbox and simulated email outbox. Events: created, assigned, technician assigned, info requested/added, SLA approaching, SLA violated, escalation, resolved, reopened, closed.
- Append-only audit trail per incident plus a global **Audit log** page.
- Search by INC-ID/text, status, priority, category, service, technician, SLA status and date range.
- Reports: totals, open, critical, SLA breaches, avg. time to acknowledge, avg. satisfaction, priority/category/service analysis, technician workload, SLA violation & escalation log, correlation alerts.
- Read-only JSON REST API: `/api/incidents`, `/api/incidents/<id>`, `/api/stats` (session-authenticated, role-scoped).
- Sample historical data is seeded on first launch so reports, suggestions and SLA monitoring have something to show (set `ITSM_DEMO_DATA=0` to start empty).

## OOP and design patterns

| Concept / pattern | Where demonstrated |
|---|---|
| Encapsulation | `Incident` owns the lifecycle rules; `SLAService`, `IntelligenceService`, `AssignmentService` expose domain operations so routes do not manipulate rules directly. |
| Abstraction / interfaces | `PriorityStrategy`, `AssignmentStrategy`, `NotificationChannel` define contracts. |
| Inheritance / polymorphism | `WeightedPriorityStrategy`/`ConservativePriorityStrategy`; `SkillBasedAssignment`/`LoadBasedAssignment`/`RoundRobinAssignment`; `InAppChannel`/`EmailChannel`; custom exception hierarchy under `ITSMError`. |
| Composition / association | `NotificationService` is composed of channels; incidents are associated with reporter and technician users; workflows collaborate with SLA, audit and notification services. |
| Collections | Dict registries for strategies, sets for valid transitions and word tokens, lists for channels. |
| Strategy pattern | Priority calculation and technician assignment, switchable at runtime from Settings. |
| Factory pattern | `IncidentFactory.create()` validates, classifies, prioritizes, applies SLA and persists; strategy registries act as simple factories. |
| Observer-style events | Lifecycle changes notify every registered `NotificationChannel` and the `AuditService`. |
| State pattern (state machine) | `Incident.VALID_TRANSITIONS`. |
| Repository pattern | `Repository` centralizes database access. |
| Adapter point | `EmailChannel` is where a real SMTP/email API would plug in. |
| Exception handling | `ValidationError`, `InvalidTransitionError`, `UnauthorizedOperationError`, `TechnicianUnavailableError`, `SLAConfigurationError` (all subclasses of `ITSMError`), converted into user-friendly messages by the routes. |

## Main structure

- `app.py` — domain classes, services, repositories, routes and database initialization.
- `templates/` — role-aware web UI.
- `static/style.css` — responsive styling.
- `tests/test_domain.py` — unit tests for strategies, SLA and categorization.
- `tests/test_workflows.py` — integration tests (temporary database) for workflows, SLA monitor, exceptions and authorization.
- `docs/` — architecture, UML, database design, requirements and test plan.

## Limitations / assumptions

- SLA clock is wall-clock time; business calendars, holidays and pause-on-pending are not modelled. The SLA monitor runs inside the web process (on requests / button), not as a separate background worker.
- Email is simulated in a persistent outbox; no real email is sent.
- Duplicate detection, correlation and suggestions are lexical heuristics, not ML; results are suggestions, not automatic merges.
- Escalation levels (Team Lead, IT Manager) are delivered to administrator accounts; there are no separate team-lead users.
- No password reset, email verification, CSRF protection or rate limiting; not configured for production deployment.

## Suggested demo sequence (three workflows)

1. **Report → prioritize → assign → notify.** Employee reports a Wi-Fi outage (auto category NETWORK, priority P1). Admin assigns with Skill + workload; technician and reporter are notified (inbox + email log).
2. **Investigate → SLA → resolve → notify.** Technician acknowledges, starts work, requests more information, sees a suggested resolution, resolves. Employee confirms the fix and rates the service; admin closes.
3. **Duplicates → correlation → escalation.** Several similar Network incidents trigger duplicate warnings and a correlation alert. The sample unacknowledged P1 email incident shows automatic escalation, SLA violations in Reports, and SLA filters in search.

Run tests: `python -m unittest discover -s tests -v` (96 tests).
