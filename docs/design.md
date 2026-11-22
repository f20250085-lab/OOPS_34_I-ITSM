# Design and Requirements Notes

## 1. Problem analysis

Organizations rely on payment, authentication, database, network, cloud and communication services. Manually tracking failures creates delayed response, inconsistent priority, poor technician allocation, missed SLAs, duplicate reports and incomplete history. I-ITSM centralizes the incident lifecycle from report through classification, prioritization, assignment, investigation, resolution, verification and closure.

### Stakeholders
- Employee/customer: reports incidents, tracks progress and reads resolution.
- Technician/support engineer: acknowledges, investigates, records notes/resolution and updates status.
- Administrator/IT manager: manages accounts, assignment, incident oversight, audit and reports.

### Assumptions
- One organization and one shared SQLite database for the prototype.
- Priorities are P1–P4 and SLA targets follow the project brief.
- The application is a demonstration, not connected to production identity, email or SMS systems.
- The administrator performs assignment; the system recommends the technician.

## 2. Functional requirements
1. Authenticate users and enforce Employee, Technician and Administrator permissions.
2. Employees and administrators can create incidents with service, description, impact, urgency, criticality and affected-user count.
3. Suggest category and calculate priority automatically.
4. Admin can assign an incident with either skill/workload or round-robin strategy.
5. Enforce lifecycle transitions and record resolution details.
6. Calculate response and resolution deadlines and display SLA state.
7. Notify relevant users in the application and record audit events.
8. Detect possible duplicates and show related incidents.
9. Search incidents by ID/text, status, priority, category, service and technician.
10. Show incident, category, priority, service, SLA, satisfaction and technician workload reports.
11. Technicians add investigation notes and request information; reporters add information and reply.
12. Reporters confirm or reopen a resolved incident and give 1–5 feedback.
13. SLA monitor warns before deadlines, records violations and escalates unacknowledged incidents (Team Lead → IT Manager).
14. Correlate several open incidents on the same service and suggest resolutions from history.
15. Admin configures SLA policies, default assignment strategy and priority strategy; activates/deactivates accounts.
16. Read-only JSON API for incidents and statistics.

## 3. Non-functional requirements
- Maintainability: business rules separated into domain/service/repository classes.
- Security: password hashes, session authentication, role checks, parameterized SQL and input validation.
- Reliability: persistent SQLite data and controlled lifecycle transitions.
- Usability: role-specific navigation, responsive layouts and clear feedback.
- Portability: Python + Flask + SQLite; no paid services.

## 4. Architecture

```mermaid
flowchart TB
  U[Employee / Technician / Admin] --> UI[Flask routes + Jinja UI]
  UI --> AUTH[Authentication & role checks]
  UI --> SVC[Domain services]
  SVC --> P[Priority Strategy]
  SVC --> A[Assignment Strategy]
  SVC --> SLA[SLA Service]
  SVC --> INT[Intelligence Service]
  SVC --> AUD[Audit + Notification Services]
  SVC --> REPO[Repository]
  REPO --> DB[(SQLite database)]
```

The prototype uses a layered modular monolith. A single Flask application is appropriate for a small student team: simple deployment, low operational overhead and clear separation between presentation, domain services and persistence. The strategy interfaces allow algorithms to change without rewriting routes.

## 5. Lifecycle and business rules

```mermaid
stateDiagram-v2
  [*] --> NEW
  NEW --> CLASSIFIED
  CLASSIFIED --> ASSIGNED
  ASSIGNED --> ACKNOWLEDGED
  ACKNOWLEDGED --> IN_PROGRESS
  IN_PROGRESS --> RESOLVED
  RESOLVED --> VERIFIED
  RESOLVED --> IN_PROGRESS: reopen
  VERIFIED --> CLOSED
```

A transition outside the defined state graph is rejected. Admin assignment records classification and assignment events. Only the assigned technician or an administrator can update technician work; employees can view only their own reports.

## 6. UML class diagram

```mermaid
classDiagram
  class ITSMError
  ITSMError <|-- ValidationError
  ITSMError <|-- InvalidTransitionError
  ITSMError <|-- UnauthorizedOperationError
  ITSMError <|-- TechnicianUnavailableError
  ITSMError <|-- SLAConfigurationError
  class Incident {
    +VALID_TRANSITIONS
    +transition(id,new_status,actor)
  }
  class IncidentFactory {
    +create(form,reporter_id)
  }
  class PriorityStrategy {
    <<interface>>
    +calculate(impact,urgency,criticality,affected_users)*
  }
  class AssignmentStrategy {
    <<interface>>
    +MAX_ACTIVE
    +choose(category)*
    +available()
  }
  class NotificationChannel {
    <<interface>>
    +send(user,incident_id,title,message)*
  }
  class NotificationService {
    +channels: list
    +notify(user_id,incident_id,title,message)
    +notify_admins(incident_id,title,message)
  }
  class AssignmentService { +assign(iid,strategy_key,actor) }
  class SLAService { +targets(priority) +deadlines(priority,created) +status(incident,at) }
  class SLAMonitor { +ESCALATION_LEVELS +run(at) +run_if_due() }
  class IntelligenceService { +suggest_category(text) +possible_duplicates() +suggest_resolutions() +correlated_groups() }
  class AuditService { +record(...) }
  class Repository { +one() +all() +run() }
  class Settings { +get(key) +set(key,value) }
  PriorityStrategy <|-- WeightedPriorityStrategy
  PriorityStrategy <|-- ConservativePriorityStrategy
  AssignmentStrategy <|-- SkillBasedAssignment
  AssignmentStrategy <|-- LoadBasedAssignment
  AssignmentStrategy <|-- RoundRobinAssignment
  NotificationChannel <|-- InAppChannel
  NotificationChannel <|-- EmailChannel
  NotificationService *-- NotificationChannel : composed of
  IncidentFactory ..> PriorityStrategy
  IncidentFactory ..> SLAService
  IncidentFactory ..> IntelligenceService
  AssignmentService ..> AssignmentStrategy
  AssignmentService ..> Incident
  SLAMonitor ..> SLAService
  SLAMonitor ..> NotificationService
  Incident ..> AuditService : records events
  Incident ..> NotificationService : emits notifications
  Repository <.. Incident : persists
```

## 7. ER model

```mermaid
erDiagram
  USERS ||--o| TECHNICIANS : profile
  USERS ||--o{ INCIDENTS : reports
  USERS o|--o{ INCIDENTS : assigned_to
  INCIDENTS ||--o{ AUDIT_LOGS : has
  INCIDENTS ||--o{ COMMENTS : has
  INCIDENTS ||--o| FEEDBACK : rated_by
  INCIDENTS ||--o{ SLA_EVENTS : has
  USERS ||--o{ NOTIFICATIONS : receives
  USERS { int id PK string name string email string password string role int active }
  TECHNICIANS { int user_id PK string skills int experience int active_count }
  INCIDENTS { int id PK int reporter_id FK int assigned_to FK string title string category string service string priority string status datetime response_due datetime resolution_due datetime acknowledged_at datetime resolved_at datetime closed_at int escalation_level }
  AUDIT_LOGS { int id PK int incident_id FK int user_id FK string action string previous_state string new_state datetime created_at }
  COMMENTS { int id PK int incident_id FK int user_id FK string kind string body }
  FEEDBACK { int incident_id PK int rating string comment }
  SLA_EVENTS { int id PK int incident_id FK string kind string detail }
  SLA_POLICIES { string priority PK int response_min int resolution_min }
  NOTIFICATIONS { int id PK int user_id FK int incident_id FK string title string message int read }
```

## 8. Technology choices
- Python: readable OOP and quick iteration for a student project.
- Flask: lightweight routing and template rendering without a large framework overhead.
- SQLite: zero-configuration persistent relational database, suitable for a local prototype.
- Jinja + CSS: server-rendered interface keeps the stack small and easy to demonstrate.
- Alternatives: Django (more batteries included), FastAPI + React (separate API/frontend), PostgreSQL (better concurrency and deployment). A larger deployment should move to PostgreSQL, use a production WSGI server, external secret management and background SLA jobs.

## 9. Use case diagram

```mermaid
flowchart LR
  E[Employee] --> U1((Login / track own incidents))
  E --> U2((Report incident))
  E --> U3((View resolution))
  T[Technician] --> U1
  T --> U4((View assigned incidents))
  T --> U5((Acknowledge / investigate))
  T --> U6((Resolve incident))
  A[Administrator] --> U1
  A --> U7((Manage users))
  A --> U8((Assign technician))
  A --> U9((Monitor SLA and audit))
  A --> U10((Search and reports))
  U2 --> U11((Auto classify and prioritize))
  U8 --> U12((Skill or round-robin strategy))
```

## 10. Data dictionary (core fields)

- `users`: identity, unique email, password hash, role and active flag.
- `technicians`: user foreign key, comma-separated skills, experience and current active workload counter.
- `incidents`: reporter and assignee foreign keys, title/description, category/service, impact/urgency/criticality, affected-user count, calculated priority, lifecycle status, timestamps, SLA deadlines and resolution text.
- `audit_logs`: incident/user references, action, description, previous/new state and timestamp.
- `notifications`: recipient, incident reference, title/body, timestamp and read flag.
- `email_outbox`: recipient address, subject/body, timestamp and `SIMULATED` delivery status.
- `settings`: key/value settings (default assignment strategy, priority strategy, round-robin pointer).
- `comments`: incident thread entries — `NOTE` (internal investigation note), `INFO_REQUEST` (to reporter), `INFO` (from reporter).
- `feedback`: one 1–5 rating and comment per incident.
- `sla_events`: warnings, response/resolution breaches and escalation levels, unique per incident and kind so each is recorded once.
- `sla_policies`: response and resolution targets (minutes) per priority, editable by the admin.
