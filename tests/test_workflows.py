"""Integration tests: run the Flask app against a throw-away SQLite database."""
import os, tempfile, unittest
from datetime import datetime, timedelta
os.environ.setdefault('ITSM_DB', os.path.join(tempfile.mkdtemp(), 'test.db'))
os.environ.setdefault('ITSM_DEMO_DATA', '0')
import app as itsm
from app import (Repository, Incident, IncidentFactory, AssignmentService, SLAService, SLAMonitor, IntelligenceService,
                 NotificationService, NotificationChannel, LoadBasedAssignment, AssignmentStrategy,
                 InvalidTransitionError, ValidationError, TechnicianUnavailableError, ITSMError)

ADMIN, EMPLOYEE, NEHA, ROHAN, ISHA = 1, 2, 3, 4, 5

def reset():
    for t in ['comments', 'feedback', 'sla_events', 'notifications', 'email_outbox', 'audit_logs', 'incidents']: Repository.run(f'DELETE FROM {t}')
    Repository.run('UPDATE technicians SET active_count=0'); Repository.run('UPDATE users SET active=1')
    Repository.run("DELETE FROM settings WHERE key='last_technician'"); itsm.SLAMonitor._last_run = 9e18   # keep the auto-monitor quiet

def make(**kw):
    form = {'title': 'Office wifi network down', 'description': 'Wifi and internet not working on floor 2', 'service': 'Network',
            'impact': 3, 'urgency': 3, 'criticality': 3, 'affected_users': 10}
    form.update(kw); return IncidentFactory.create(form, EMPLOYEE)[0]

def login(client, email, password):
    return client.post('/login', data={'email': email, 'password': password})

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        reset(); self.client = itsm.app.test_client()

    # --- Normal operations ---
    def test_factory_autoclassifies_and_prioritises(self):
        iid = make(impact=4, urgency=4, criticality=3, affected_users=200)
        row = Repository.one('SELECT * FROM incidents WHERE id=?', (iid,))
        self.assertEqual((row['category'], row['priority'], row['status']), ('NETWORK', 'P1', 'NEW'))
        self.assertTrue(Repository.one("SELECT 1 FROM notifications WHERE user_id=? AND incident_id=?", (ADMIN, iid)))

    def test_full_lifecycle_to_closed(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN)
        for s in ['ACKNOWLEDGED', 'IN_PROGRESS', 'RESOLVED', 'VERIFIED', 'CLOSED']: Incident.transition(iid, s, ADMIN)
        row = Repository.one('SELECT * FROM incidents WHERE id=?', (iid,))
        self.assertEqual(row['status'], 'CLOSED'); self.assertIsNotNone(row['acknowledged_at']); self.assertIsNotNone(row['closed_at'])
        self.assertEqual(Repository.one('SELECT active_count FROM technicians WHERE user_id=?', (row['assigned_to'],))['active_count'], 0)

    def test_skill_assignment_picks_network_expert(self):
        iid = make(); self.assertEqual(AssignmentService.assign(iid, 'skill', ADMIN), 'Neha Network')

    def test_load_based_picks_least_loaded(self):
        Repository.run('UPDATE technicians SET active_count=2 WHERE user_id IN (?,?)', (NEHA, ROHAN))
        self.assertEqual(LoadBasedAssignment().choose('NETWORK'), ISHA)

    def test_round_robin_rotates(self):
        a, b = make(), make(title='Printer jammed in HR office', description='Printer in HR office is jammed and not printing')
        AssignmentService.assign(a, 'round', ADMIN); AssignmentService.assign(b, 'round', ADMIN)
        techs = [Repository.one('SELECT assigned_to FROM incidents WHERE id=?', (i,))['assigned_to'] for i in (a, b)]
        self.assertEqual(techs, [NEHA, ROHAN])

    def test_notifications_go_to_every_channel(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN)
        self.assertTrue(Repository.one('SELECT 1 FROM notifications WHERE user_id=? AND incident_id=?', (NEHA, iid)))
        self.assertTrue(Repository.one("SELECT 1 FROM email_outbox WHERE to_email='bitsneha@itsm.demo'"))

    def test_new_channel_can_be_plugged_in(self):
        sent = []
        class FakeSMS(NotificationChannel):
            def send(self, user, incident_id, title, message): sent.append(user['id'])
        NotificationService.channels.append(FakeSMS())
        try: NotificationService.notify(NEHA, None, 't', 'm')
        finally: NotificationService.channels.pop()
        self.assertEqual(sent, [NEHA])

    def test_suggested_resolution_from_history(self):
        old = make(title='Office wifi disconnecting in HR department', description='Wifi network keeps disconnecting, internet not working')
        Repository.run("UPDATE incidents SET status='CLOSED', resolution='Replaced faulty access point' WHERE id=?", (old,))
        new = make()
        suggestions = IntelligenceService.suggest_resolutions('Office wifi network down', 'Wifi and internet not working on floor 2', 'NETWORK', new)
        self.assertEqual(suggestions[0]['id'], old)

    def test_duplicate_detection(self):
        a = make(); self.assertIn(a, [m[0] for m in IntelligenceService.possible_duplicates('Office wifi network down', 'Wifi and internet not working on floor 2')])

    def test_correlation_needs_three_on_same_service(self):
        make(); make(title='Internet slow on floor three', description='Very slow internet in the finance office today')
        self.assertEqual(len(IntelligenceService.correlated_groups()), 0)
        make(title='LAN port dead in room 204', description='Ethernet LAN port is not giving any connection')
        self.assertEqual(IntelligenceService.correlated_groups()[0]['service'], 'Network')

    # --- SLA monitoring / escalation ---
    def test_sla_warning_breach_and_escalation(self):
        iid = make(impact=5, urgency=5, criticality=5, affected_users=100)   # P1: 5 min response, 30 min resolution
        created = datetime.fromisoformat(Repository.one('SELECT created_at FROM incidents WHERE id=?', (iid,))['created_at'])
        SLAMonitor.run(at=created + timedelta(minutes=6))
        kinds = {r['kind'] for r in Repository.all('SELECT kind FROM sla_events WHERE incident_id=?', (iid,))}
        self.assertIn('RESPONSE_BREACH', kinds); self.assertIn('ESCALATED_L1', kinds)
        SLAMonitor.run(at=created + timedelta(minutes=31))
        kinds = {r['kind'] for r in Repository.all('SELECT kind FROM sla_events WHERE incident_id=?', (iid,))}
        self.assertTrue({'ESCALATED_L2', 'RESOLUTION_BREACH'} <= kinds)
        self.assertEqual(Repository.one('SELECT escalation_level FROM incidents WHERE id=?', (iid,))['escalation_level'], 2)

    def test_sla_events_recorded_once(self):
        iid = make(impact=5, urgency=5, criticality=5)
        created = datetime.fromisoformat(Repository.one('SELECT created_at FROM incidents WHERE id=?', (iid,))['created_at'])
        SLAMonitor.run(at=created + timedelta(minutes=40)); first = Repository.one('SELECT COUNT(*) n FROM sla_events')['n']
        self.assertEqual(SLAMonitor.run(at=created + timedelta(minutes=41)), 0)
        self.assertEqual(Repository.one('SELECT COUNT(*) n FROM sla_events')['n'], first)

    def test_sla_status_on_track_then_at_risk(self):
        iid = make(impact=2, urgency=2, criticality=2, affected_users=1)   # P3: 60 min response / 480 min resolution
        Repository.run("UPDATE incidents SET acknowledged_at=created_at WHERE id=?", (iid,)); row = Repository.one('SELECT * FROM incidents WHERE id=?', (iid,))
        created = datetime.fromisoformat(row['created_at'])
        self.assertEqual(SLAService.status(row, created + timedelta(minutes=10)), 'ON TRACK')
        self.assertEqual(SLAService.status(row, created + timedelta(minutes=460)), 'AT RISK')
        self.assertEqual(SLAService.status(row, created + timedelta(minutes=481)), 'BREACHED')

    def test_admin_can_change_sla_policy(self):
        Repository.run("UPDATE sla_policies SET response_min=10,resolution_min=60 WHERE priority='P1'")
        try:
            r, d = SLAService.deadlines('P1', datetime(2026, 1, 1, 10, 0))
            self.assertEqual(((r - datetime(2026, 1, 1, 10)).seconds, (d - datetime(2026, 1, 1, 10)).seconds), (600, 3600))
        finally: Repository.run("UPDATE sla_policies SET response_min=5,resolution_min=30 WHERE priority='P1'")

    # --- Invalid input / boundary / exceptions ---
    def test_invalid_impact_rejected(self):
        with self.assertRaises(ValidationError): make(impact=9)
        with self.assertRaises(ValidationError): make(urgency='abc')

    def test_boundary_scores_accepted(self):
        self.assertEqual(Repository.one('SELECT priority FROM incidents WHERE id=?', (make(impact=1, urgency=1, criticality=1, affected_users=1),))['priority'], 'P4')

    def test_short_title_rejected(self):
        with self.assertRaises(ValidationError): make(title='abc')

    def test_unknown_service_rejected(self):
        with self.assertRaises(ValidationError): make(service='Teleporter')

    def test_new_to_closed_raises_custom_exception(self):
        iid = make()
        with self.assertRaises(InvalidTransitionError): Incident.transition(iid, 'CLOSED', ADMIN)
        self.assertTrue(issubclass(InvalidTransitionError, ITSMError))

    def test_workload_limit_raises_technician_unavailable(self):
        Repository.run('UPDATE technicians SET active_count=?', (AssignmentStrategy.MAX_ACTIVE,))
        with self.assertRaises(TechnicianUnavailableError): AssignmentService.assign(make(), 'skill', ADMIN)

    def test_cannot_assign_twice(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN)
        with self.assertRaises(InvalidTransitionError): AssignmentService.assign(iid, 'skill', ADMIN)

    # --- Authorization (through the web layer) ---
    def test_invalid_login_rejected(self):
        self.assertIn(b'Invalid email or password', self.client.post('/login', data={'email': 'bitsadmin@itsm.demo', 'password': 'wrong'}).data)

    def test_employee_cannot_open_reports(self):
        login(self.client, 'bitsemployee@itsm.demo', 'BITSuser123')
        self.assertEqual(self.client.get('/reports').status_code, 403)

    def test_technician_cannot_see_others_incident(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN)   # goes to Neha
        login(self.client, 'bitsrohan@itsm.demo', 'BITStech123')
        self.assertEqual(self.client.get(f'/incidents/{iid}').status_code, 403)

    def test_technician_cannot_close(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN)
        for s in ['ACKNOWLEDGED', 'IN_PROGRESS', 'RESOLVED', 'VERIFIED']: Incident.transition(iid, s, ADMIN)
        login(self.client, 'bitsneha@itsm.demo', 'BITStech123')
        self.client.post(f'/incidents/{iid}/transition', data={'status': 'CLOSED'})
        self.assertEqual(Repository.one('SELECT status FROM incidents WHERE id=?', (iid,))['status'], 'VERIFIED')

    def test_api_requires_login_and_is_scoped(self):
        self.assertEqual(self.client.get('/api/incidents').status_code, 401)
        make(); login(self.client, 'bitsneha@itsm.demo', 'BITStech123')
        self.assertEqual(self.client.get('/api/incidents').get_json(), [])

    # --- Employee features ---
    def test_info_request_and_reply(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN)
        login(self.client, 'bitsneha@itsm.demo', 'BITStech123')
        self.client.post(f'/incidents/{iid}/comment', data={'kind': 'INFO_REQUEST', 'body': 'Which room are you in?'})
        self.assertTrue(Repository.one("SELECT 1 FROM notifications WHERE user_id=? AND title='Information requested'", (EMPLOYEE,)))
        emp = itsm.app.test_client(); login(emp, 'bitsemployee@itsm.demo', 'BITSuser123')
        emp.post(f'/incidents/{iid}/comment', data={'kind': 'INFO', 'body': 'Desk 214, floor 2'})
        emp.post(f'/incidents/{iid}/comment', data={'kind': 'NOTE', 'body': 'sneaky internal note'})   # not allowed for employees
        self.assertEqual([r['kind'] for r in Repository.all('SELECT kind FROM comments WHERE incident_id=? ORDER BY id', (iid,))], ['INFO_REQUEST', 'INFO'])

    def test_feedback_only_after_resolution_and_once(self):
        iid = make(); login(self.client, 'bitsemployee@itsm.demo', 'BITSuser123')
        self.client.post(f'/incidents/{iid}/feedback', data={'rating': 5})
        self.assertIsNone(Repository.one('SELECT 1 FROM feedback WHERE incident_id=?', (iid,)))
        Repository.run("UPDATE incidents SET status='RESOLVED' WHERE id=?", (iid,))
        self.client.post(f'/incidents/{iid}/feedback', data={'rating': 4}); self.client.post(f'/incidents/{iid}/feedback', data={'rating': 1})
        self.assertEqual(Repository.one('SELECT rating FROM feedback WHERE incident_id=?', (iid,))['rating'], 4)

    def test_registration_creates_employee(self):
        self.client.post('/register', data={'name': 'New Hire', 'email': 'newhire@company.example', 'password': 'secret1'})
        row = Repository.one("SELECT role FROM users WHERE email='newhire@company.example'")
        self.assertEqual(row['role'], 'EMPLOYEE'); Repository.run("DELETE FROM users WHERE email='newhire@company.example'")

    def test_search_by_sla_and_id(self):
        iid = make(); login(self.client, 'bitsadmin@itsm.demo', 'BITSadmin123')
        self.assertIn(b'Office wifi', self.client.get(f'/search?q=INC-{iid:04d}').data)
        self.assertNotIn(b'Office wifi', self.client.get('/search?sla=BREACHED').data)

    def test_all_admin_pages_render(self):
        make(); login(self.client, 'bitsadmin@itsm.demo', 'BITSadmin123')
        for page in ['/dashboard', '/search', '/reports', '/admin/users', '/admin/settings', '/admin/audit', '/admin/email-log', '/notifications', '/incidents/new', '/api/stats']:
            self.assertEqual(self.client.get(page).status_code, 200, page)

if __name__ == '__main__': unittest.main()
