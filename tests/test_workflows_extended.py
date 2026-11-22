"""Extended workflow tests: authentication, authorization, lifecycle routes, settings and administration."""
import unittest
from test_workflows import reset, make, login, ADMIN, EMPLOYEE, NEHA, ROHAN, ISHA
import app as itsm
from app import Repository, Incident, IncidentFactory, AssignmentService, Settings

FORM = {'title': 'Payroll portal unavailable', 'description': 'Employee Portal shows a 500 error for payroll', 'service': 'Employee Portal',
        'impact': 3, 'urgency': 3, 'criticality': 3, 'affected_users': 10}
def walk(iid, *states):
    for s in states: Incident.transition(iid, s, ADMIN)

class ExtendedWorkflowTests(unittest.TestCase):
    def setUp(self): reset(); self.client = itsm.app.test_client()
    def as_(self, who):
        c = itsm.app.test_client(); creds = {'admin': ('bitsadmin@itsm.demo', 'BITSadmin123'), 'emp': ('bitsemployee@itsm.demo', 'BITSuser123'),
                                             'neha': ('bitsneha@itsm.demo', 'BITStech123'), 'rohan': ('bitsrohan@itsm.demo', 'BITStech123')}[who]
        login(c, *creds); return c
    def resolved_incident(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN); walk(iid, 'ACKNOWLEDGED', 'IN_PROGRESS', 'RESOLVED'); return iid
    def policy_form(self, **over):
        d = {'assignment_strategy': 'skill', 'priority_strategy': 'weighted'}
        for p in Repository.all('SELECT * FROM sla_policies'): d[f"{p['priority']}_response"] = p['response_min']; d[f"{p['priority']}_resolution"] = p['resolution_min']
        d.update(over); return d

    # --- authentication ---
    def test_valid_login_redirects_to_dashboard(self):
        r = login(self.client, 'bitsemployee@itsm.demo', 'BITSuser123'); self.assertEqual(r.status_code, 302); self.assertTrue(r.headers['Location'].endswith('/dashboard'))
    def test_deactivated_account_cannot_sign_in(self):
        Repository.run('UPDATE users SET active=0 WHERE id=?', (EMPLOYEE,))
        self.assertIn(b'Invalid email or password', login(self.client, 'bitsemployee@itsm.demo', 'BITSuser123').data)
    def test_logout_ends_the_session(self):
        login(self.client, 'bitsadmin@itsm.demo', 'BITSadmin123'); self.client.get('/logout')
        r = self.client.get('/dashboard'); self.assertEqual(r.status_code, 302); self.assertIn('/login', r.headers['Location'])
    def test_duplicate_registration_email_rejected(self):
        r = self.client.post('/register', data={'name': 'Copy Cat', 'email': 'bitsemployee@itsm.demo', 'password': 'secret1'})
        self.assertIn(b'already exists', r.data); self.assertEqual(Repository.one("SELECT COUNT(*) n FROM users WHERE email='bitsemployee@itsm.demo'")['n'], 1)
    def test_short_registration_password_rejected(self):
        r = self.client.post('/register', data={'name': 'Short Pass', 'email': 'short@company.example', 'password': 'abc'})
        self.assertIn(b'at least 6 characters', r.data); self.assertIsNone(Repository.one("SELECT 1 FROM users WHERE email='short@company.example'"))

    # --- visibility and API ---
    def test_employee_cannot_open_an_incident_reported_by_someone_else(self):
        iid = IncidentFactory.create(FORM, ADMIN)[0]; self.assertEqual(self.as_('emp').get(f'/incidents/{iid}').status_code, 403)
    def test_employee_api_lists_only_own_incidents(self):
        make(); IncidentFactory.create(FORM, ADMIN)
        data = self.as_('emp').get('/api/incidents').get_json(); self.assertEqual(len(data), 1); self.assertEqual(data[0]['reporter_id'], EMPLOYEE)
    def test_api_stats_for_admin(self):
        make(); data = self.as_('admin').get('/api/stats').get_json(); self.assertEqual((data['total'], data['open'], data['by_priority']), (1, 1, {'P1': 1}))
    def test_api_stats_forbidden_for_employee(self): self.assertEqual(self.as_('emp').get('/api/stats').status_code, 403)
    def test_api_incident_detail_includes_code_and_sla(self):
        iid = make(); d = self.as_('admin').get(f'/api/incidents/{iid}').get_json(); self.assertEqual(d['code'], f'INC-{iid:04d}'); self.assertIn('sla_status', d)

    # --- assignment and resolution routes ---
    def test_admin_assigns_with_least_loaded_strategy(self):
        iid = make(); self.as_('admin').post(f'/incidents/{iid}/assign', data={'strategy': 'load'})
        row = Repository.one('SELECT status,assigned_to FROM incidents WHERE id=?', (iid,)); self.assertEqual((row['status'], row['assigned_to']), ('ASSIGNED', NEHA))
    def test_employee_cannot_assign(self):
        iid = make(); self.assertEqual(self.as_('emp').post(f'/incidents/{iid}/assign', data={'strategy': 'skill'}).status_code, 403)
        self.assertEqual(Repository.one('SELECT status FROM incidents WHERE id=?', (iid,))['status'], 'NEW')
    def test_cannot_resolve_before_work_starts(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN)
        self.as_('neha').post(f'/incidents/{iid}/resolve', data={'resolution': 'Restarted the network gateway'})
        self.assertEqual(Repository.one('SELECT status FROM incidents WHERE id=?', (iid,))['status'], 'ASSIGNED')
    def test_resolution_text_must_be_long_enough(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN); walk(iid, 'ACKNOWLEDGED', 'IN_PROGRESS')
        self.as_('neha').post(f'/incidents/{iid}/resolve', data={'resolution': 'fixed'})
        self.assertEqual(Repository.one('SELECT status FROM incidents WHERE id=?', (iid,))['status'], 'IN_PROGRESS')
    def test_resolving_records_text_and_notifies_reporter(self):
        iid = make(); AssignmentService.assign(iid, 'skill', ADMIN); walk(iid, 'ACKNOWLEDGED', 'IN_PROGRESS')
        self.as_('neha').post(f'/incidents/{iid}/resolve', data={'resolution': 'Restarted the mail gateway service'})
        row = Repository.one('SELECT status,resolution,resolved_at FROM incidents WHERE id=?', (iid,))
        self.assertEqual((row['status'], row['resolution']), ('RESOLVED', 'Restarted the mail gateway service')); self.assertIsNotNone(row['resolved_at'])
        self.assertTrue(Repository.one("SELECT 1 FROM notifications WHERE user_id=? AND title='Incident resolved'", (EMPLOYEE,)))
    def test_reporter_confirms_fix(self):
        iid = self.resolved_incident(); self.as_('emp').post(f'/incidents/{iid}/transition', data={'status': 'VERIFIED'})
        self.assertEqual(Repository.one('SELECT status FROM incidents WHERE id=?', (iid,))['status'], 'VERIFIED')
    def test_reporter_reopens_and_technician_is_notified(self):
        iid = self.resolved_incident(); self.as_('emp').post(f'/incidents/{iid}/transition', data={'status': 'IN_PROGRESS'})
        row = Repository.one('SELECT status,resolved_at FROM incidents WHERE id=?', (iid,)); self.assertEqual(row['status'], 'IN_PROGRESS'); self.assertIsNone(row['resolved_at'])
        self.assertTrue(Repository.one("SELECT 1 FROM notifications WHERE user_id=? AND title='Incident reopened'", (NEHA,)))
    def test_reporter_cannot_close_incident(self):
        iid = self.resolved_incident(); self.as_('emp').post(f'/incidents/{iid}/transition', data={'status': 'CLOSED'})
        self.assertEqual(Repository.one('SELECT status FROM incidents WHERE id=?', (iid,))['status'], 'RESOLVED')

    # --- comments ---
    def test_closed_incident_rejects_new_comments(self):
        iid = self.resolved_incident(); walk(iid, 'VERIFIED', 'CLOSED')
        self.as_('emp').post(f'/incidents/{iid}/comment', data={'kind': 'INFO', 'body': 'One more thing'})
        self.assertEqual(Repository.one('SELECT COUNT(*) n FROM comments WHERE incident_id=?', (iid,))['n'], 0)
    def test_too_short_comment_rejected(self):
        iid = make(); self.as_('emp').post(f'/incidents/{iid}/comment', data={'kind': 'INFO', 'body': 'ok'})
        self.assertEqual(Repository.one('SELECT COUNT(*) n FROM comments')['n'], 0)

    # --- settings and administration ---
    def test_invalid_sla_policy_is_rejected(self):
        self.as_('admin').post('/admin/settings', data=self.policy_form(P1_response=50, P1_resolution=10))
        p = Repository.one("SELECT response_min,resolution_min FROM sla_policies WHERE priority='P1'"); self.assertEqual((p['response_min'], p['resolution_min']), (5, 30))
    def test_switching_priority_strategy_changes_new_incidents(self):
        self.addCleanup(Settings.set, 'priority_strategy', 'weighted')
        self.as_('admin').post('/admin/settings', data=self.policy_form(priority_strategy='conservative'))
        iid = make(impact=3, urgency=3, criticality=3, affected_users=1)     # weighted would give P1 (27); additive gives 9 → P2
        self.assertEqual(Repository.one('SELECT priority FROM incidents WHERE id=?', (iid,))['priority'], 'P2')
    def test_admin_cannot_deactivate_own_account(self):
        self.as_('admin').post('/admin/users', data={'action': 'toggle', 'user_id': ADMIN})
        self.assertEqual(Repository.one('SELECT active FROM users WHERE id=?', (ADMIN,))['active'], 1)
    def test_admin_creates_technician_with_skills(self):
        self.addCleanup(lambda: (Repository.run("DELETE FROM technicians WHERE user_id IN (SELECT id FROM users WHERE email='new.tech@company.example')"), Repository.run("DELETE FROM users WHERE email='new.tech@company.example'")))
        self.as_('admin').post('/admin/users', data={'name': 'New Tech', 'email': 'new.tech@company.example', 'password': 'secret1', 'role': 'TECHNICIAN', 'skills': 'SECURITY', 'experience': '2'})
        t = Repository.one("SELECT t.skills,t.experience FROM technicians t JOIN users u ON u.id=t.user_id WHERE u.email='new.tech@company.example'")
        self.assertEqual((t['skills'], t['experience']), ('SECURITY', 2))

if __name__ == '__main__': unittest.main()
