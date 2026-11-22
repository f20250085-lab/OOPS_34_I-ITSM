# Test plan

96 automated tests: `python -m unittest discover -s tests -v`. Tests use a temporary database, so they never touch `itsm.db`.

| ID | Type | Scenario | Expected result | Automated test |
|---|---|---|---|---|
| TC01 | Normal | Login with valid credentials | Dashboard opens | (manual) |
| TC02 | Invalid input | Login with wrong password | "Invalid email or password" | `test_invalid_login_rejected` |
| TC03 | Normal | Self-registration | New EMPLOYEE account created | `test_registration_creates_employee` |
| TC04 | Normal | Create incident with network keywords and high scores | Category NETWORK, priority P1, admins notified | `test_factory_autoclassifies_and_prioritises` |
| TC05 | Invalid input | Title shorter than 5 characters | `ValidationError`, no record | `test_short_title_rejected` |
| TC06 | Boundary | Impact 9 / non-numeric urgency | `ValidationError` | `test_invalid_impact_rejected` |
| TC07 | Boundary | All scores at minimum (1,1,1,1) | Accepted, P4 | `test_boundary_scores_accepted` |
| TC08 | Invalid input | Unknown service | `ValidationError` ("Service not found") | `test_unknown_service_rejected` |
| TC09 | Business rule | Weighted priority thresholds P1–P4, users capped at 100 | Correct priority per score | `test_weighted_*`, `test_users_capped` |
| TC10 | Business rule | SLA targets P1–P4 | Deadlines match policy | `test_p1_sla` … `test_p4_sla` |
| TC11 | Business rule | Admin edits SLA policy | New deadlines used | `test_admin_can_change_sla_policy` |
| TC12 | Normal | Skill-based assignment for NETWORK | Network technician chosen | `test_skill_assignment_picks_network_expert` |
| TC13 | Normal | Load-based assignment | Least-loaded technician chosen | `test_load_based_picks_least_loaded` |
| TC14 | Normal | Round robin over two incidents | Technicians rotate | `test_round_robin_rotates` |
| TC15 | Exception | All technicians at workload limit | `TechnicianUnavailableError` | `test_workload_limit_raises_technician_unavailable` |
| TC16 | Exception | Assign an already-assigned incident | `InvalidTransitionError` | `test_cannot_assign_twice` |
| TC17 | Exception | NEW → CLOSED | `InvalidTransitionError` | `test_new_to_closed_raises_custom_exception` |
| TC18 | Normal | Full lifecycle to CLOSED | Timestamps set, workload decremented | `test_full_lifecycle_to_closed` |
| TC19 | Authorization | Technician tries to close | Rejected, status unchanged | `test_technician_cannot_close` |
| TC20 | Authorization | Technician opens another technician's incident | HTTP 403 | `test_technician_cannot_see_others_incident` |
| TC21 | Authorization | Employee opens Reports | HTTP 403 | `test_employee_cannot_open_reports` |
| TC22 | Authorization | API without login / technician sees only own | 401 / scoped list | `test_api_requires_login_and_is_scoped` |
| TC23 | Normal | Technician requests info, employee replies; employee cannot post internal notes | Notification sent, note rejected | `test_info_request_and_reply` |
| TC24 | Business rule | Feedback before resolution / twice | Rejected; only first rating stored | `test_feedback_only_after_resolution_and_once` |
| TC25 | Normal | Notifications reach in-app and email channels | Both recorded | `test_notifications_go_to_every_channel` |
| TC26 | Extensibility | Plug in a new channel class | New channel receives notifications | `test_new_channel_can_be_plugged_in` |
| TC27 | Intelligent | Similar open incident exists | Reported as duplicate | `test_duplicate_detection` |
| TC28 | Intelligent | 3 open incidents on one service within 1 hour | Correlation group returned (not for 2) | `test_correlation_needs_three_on_same_service` |
| TC29 | Intelligent | Similar closed incident with resolution | Suggested resolution shown | `test_suggested_resolution_from_history` |
| TC30 | SLA | P1 unacknowledged after 6 and 31 minutes | Response breach, L1 then L2 escalation, resolution breach | `test_sla_warning_breach_and_escalation` |
| TC31 | SLA | Monitor runs twice | Events recorded only once | `test_sla_events_recorded_once` |
| TC32 | SLA | P3 status over time | ON TRACK → AT RISK → BREACHED | `test_sla_status_on_track_then_at_risk` |
| TC33 | Normal | Search by INC-ID and SLA status | Correct results | `test_search_by_sla_and_id` |
| TC34 | Smoke | Every admin page renders | HTTP 200 | `test_all_admin_pages_render` |
| TC35 | Intelligent | Category keywords (network, hardware, database, default) | Correct category | `test_category_*` |
| TC36 | Boundary | Weighted score exactly at / just under each P2, P3, P4 threshold; users lift a score | Correct priority at every boundary | `PriorityBoundaryTests` (`test_weighted_*`) |
| TC37 | Boundary | Conservative strategy: score 11 needed for P1; +2 bonus at exactly 50 users | P2 → P1 at the threshold | `test_conservative_p1_needs_score_eleven`, `test_conservative_bonus_at_fifty_users` |
| TC38 | Design | Priority strategy base class is abstract; subclasses implement it | `NotImplementedError` from base | `test_priority_base_class_is_abstract` |
| TC39 | Intelligent | Category keywords for authentication, security, storage and cloud | Correct category | `ClassificationTests` |
| TC40 | Intelligent | Text similarity: identical, unrelated, partial overlap | 1.0, 0.0, 0.5 | `TextSimilarityTests` |
| TC41 | Business rule | State machine has 8 states, CLOSED is terminal, only RESOLVED has two exits | As specified | `LifecycleAndErrorTests` |
| TC42 | Design | Custom exception hierarchy | All derive from `ITSMError` | `test_exception_hierarchy` |
| TC43 | SLA | Default targets grow with lower priority; unknown priority has no policy | Ordered targets; `SLAConfigurationError` | `SLAMathTests` |
| TC44 | SLA | Resolved before / after the deadline; unacknowledged past response target | MET / BREACHED / BREACHED | `SLAMathTests` (`test_resolved_*`, `test_unacknowledged_*`) |
| TC45 | Normal | Valid login redirects to the dashboard | HTTP 302 to /dashboard | `test_valid_login_redirects_to_dashboard` |
| TC46 | Authorization | Deactivated account tries to sign in | Rejected | `test_deactivated_account_cannot_sign_in` |
| TC47 | Security | Logout, then open the dashboard | Redirected to login | `test_logout_ends_the_session` |
| TC48 | Invalid input | Register with an existing email / a 3-character password | Rejected, no account created | `test_duplicate_registration_email_rejected`, `test_short_registration_password_rejected` |
| TC49 | Authorization | Employee opens another reporter's incident; employee API is scoped | 403; only own incidents | `test_employee_cannot_open_an_incident_reported_by_someone_else`, `test_employee_api_lists_only_own_incidents` |
| TC50 | Normal | Admin stats API; employee blocked; incident JSON has code and SLA | Correct totals; 403; fields present | `test_api_stats_*`, `test_api_incident_detail_includes_code_and_sla` |
| TC51 | Normal | Admin assigns with least-loaded strategy through the web route | ASSIGNED to the free technician | `test_admin_assigns_with_least_loaded_strategy` |
| TC52 | Authorization | Employee tries to assign | HTTP 403, status unchanged | `test_employee_cannot_assign` |
| TC53 | Business rule | Resolve before IN_PROGRESS / with a too-short resolution | Rejected, status unchanged | `test_cannot_resolve_before_work_starts`, `test_resolution_text_must_be_long_enough` |
| TC54 | Normal | Technician resolves with notes | RESOLVED, text and time saved, reporter notified | `test_resolving_records_text_and_notifies_reporter` |
| TC55 | Normal | Reporter confirms the fix / reopens it | VERIFIED / IN_PROGRESS, technician notified | `test_reporter_confirms_fix`, `test_reporter_reopens_and_technician_is_notified` |
| TC56 | Authorization | Reporter tries to close a resolved incident | Rejected, still RESOLVED | `test_reporter_cannot_close_incident` |
| TC57 | Business rule | Comment on a closed incident / a 2-character comment | Rejected, nothing stored | `test_closed_incident_rejects_new_comments`, `test_too_short_comment_rejected` |
| TC58 | Invalid input | Admin saves an SLA policy where response ≥ resolution | Rejected, old policy kept | `test_invalid_sla_policy_is_rejected` |
| TC59 | Strategy | Admin switches priority strategy to conservative | New incidents use the additive score | `test_switching_priority_strategy_changes_new_incidents` |
| TC60 | Administration | Admin deactivates own account / creates a technician with skills | Refused / technician row created | `test_admin_cannot_deactivate_own_account`, `test_admin_creates_technician_with_skills` |
