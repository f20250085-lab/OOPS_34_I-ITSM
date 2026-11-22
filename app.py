"""I-ITSM: Intelligent IT Service Management demo application (BITS Pilani, Hyderabad Campus)."""
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
import os, re, sqlite3, time
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get('ITSM_DB', BASE / 'itsm.db'))
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-this-secret')

CATEGORIES = ['APPLICATION', 'NETWORK', 'HARDWARE', 'DATABASE', 'CLOUD', 'AUTHENTICATION', 'SECURITY', 'STORAGE']
SERVICES = ['General IT', 'Payment', 'Network', 'Email', 'Authentication', 'Database', 'Cloud Storage', 'Employee Portal']
STATUSES = ['NEW', 'CLASSIFIED', 'ASSIGNED', 'ACKNOWLEDGED', 'IN_PROGRESS', 'RESOLVED', 'VERIFIED', 'CLOSED']
OPEN_STATUSES = ('NEW', 'CLASSIFIED', 'ASSIGNED', 'ACKNOWLEDGED', 'IN_PROGRESS')

def now_iso(): return datetime.now().isoformat(timespec='seconds')

# ---------- Custom exceptions ----------
class ITSMError(Exception):
    """Base class for all business-rule errors raised by the domain layer."""
class ValidationError(ITSMError, ValueError):
    """Invalid user input (bad title, out-of-range impact, ...)."""
class InvalidTransitionError(ITSMError, ValueError):
    """Lifecycle transition not allowed by the incident state machine."""
class UnauthorizedOperationError(ITSMError):
    """User's role is not allowed to perform this operation."""
class TechnicianUnavailableError(ITSMError):
    """No active technician with spare capacity could be found."""
class SLAConfigurationError(ITSMError):
    """No SLA policy is configured for a priority."""

# ---------- Persistence / Repository Pattern ----------
class Database:
    @staticmethod
    def connect():
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        return con

class Repository:
    """Small repository abstraction keeps SQL out of domain services."""
    @staticmethod
    def one(sql, args=()):
        with Database.connect() as db: return db.execute(sql, args).fetchone()
    @staticmethod
    def all(sql, args=()):
        with Database.connect() as db: return db.execute(sql, args).fetchall()
    @staticmethod
    def run(sql, args=()):
        with Database.connect() as db:
            cur = db.execute(sql, args); db.commit(); return cur.lastrowid

class Settings:
    """Key/value configuration stored in the database (assignment strategy, priority strategy...)."""
    @staticmethod
    def get(key, default=None):
        row = Repository.one('SELECT value FROM settings WHERE key=?', (key,))
        return row['value'] if row else default
    @staticmethod
    def set(key, value):
        Repository.run('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, str(value)))

# ---------- Observers: audit + notification channels ----------
class AuditService:
    @staticmethod
    def record(incident_id, user_id, action, description, previous=None, new=None):
        Repository.run('''INSERT INTO audit_logs(incident_id,user_id,action,description,previous_state,new_state,created_at)
          VALUES(?,?,?,?,?,?,?)''', (incident_id, user_id, action, description, previous, new, now_iso()))

class NotificationChannel:
    """Interface: every notification mechanism implements send(). Add SMS/push by writing one more subclass."""
    name = 'BASE'
    def send(self, user, incident_id, title, message): raise NotImplementedError

class InAppChannel(NotificationChannel):
    name = 'IN_APP'
    def send(self, user, incident_id, title, message):
        Repository.run('INSERT INTO notifications(user_id,incident_id,title,message,created_at) VALUES(?,?,?,?,?)',
                       (user['id'], incident_id, title, message, now_iso()))

class EmailChannel(NotificationChannel):
    """Adapter point for a real SMTP/email API. For the prototype the email is written to a simulated outbox."""
    name = 'EMAIL'
    def send(self, user, incident_id, title, message):
        Repository.run('INSERT INTO email_outbox(to_email,subject,body,created_at,status) VALUES(?,?,?,?,?)',
                       (user['email'], title, message, now_iso(), 'SIMULATED'))

class NotificationService:
    """Subject in an observer-style design: notifies every registered channel."""
    channels = [InAppChannel(), EmailChannel()]
    @classmethod
    def notify(cls, user_id, incident_id, title, message):
        user = Repository.one('SELECT id,email FROM users WHERE id=?', (user_id,))
        if not user: return
        for channel in cls.channels: channel.send(user, incident_id, title, message)
    @classmethod
    def notify_admins(cls, incident_id, title, message):
        for admin in Repository.all("SELECT id FROM users WHERE role='ADMIN' AND active=1"):
            cls.notify(admin['id'], incident_id, title, message)

# ---------- Domain model ----------
class Incident:
    """Incident lifecycle as a state machine. Invalid jumps raise InvalidTransitionError."""
    VALID_TRANSITIONS = {
        'NEW': {'CLASSIFIED'}, 'CLASSIFIED': {'ASSIGNED'},
        'ASSIGNED': {'ACKNOWLEDGED'}, 'ACKNOWLEDGED': {'IN_PROGRESS'},
        'IN_PROGRESS': {'RESOLVED'}, 'RESOLVED': {'VERIFIED', 'IN_PROGRESS'},
        'VERIFIED': {'CLOSED'}, 'CLOSED': set()
    }
    TIMESTAMP_COLUMN = {'ACKNOWLEDGED': 'acknowledged_at', 'RESOLVED': 'resolved_at', 'CLOSED': 'closed_at'}

    @classmethod
    def transition(cls, incident_id, new_status, actor):
        item = Repository.one('SELECT * FROM incidents WHERE id=?', (incident_id,))
        if not item: raise ITSMError('Incident not found')
        old = item['status']
        if new_status not in cls.VALID_TRANSITIONS.get(old, set()):
            raise InvalidTransitionError(f'Invalid transition: {old} → {new_status}')
        Repository.run('UPDATE incidents SET status=?, updated_at=? WHERE id=?', (new_status, now_iso(), incident_id))
        if new_status in cls.TIMESTAMP_COLUMN:
            Repository.run(f'UPDATE incidents SET {cls.TIMESTAMP_COLUMN[new_status]}=? WHERE id=?', (now_iso(), incident_id))
        if new_status == 'IN_PROGRESS' and old == 'RESOLVED':
            Repository.run('UPDATE incidents SET resolved_at=NULL WHERE id=?', (incident_id,))
        AuditService.record(incident_id, actor, 'STATUS_CHANGED', f'{old} → {new_status}', old, new_status)
        tag = f'INC-{incident_id:04d}'
        if new_status == 'CLOSED' and item['assigned_to']:
            Repository.run('UPDATE technicians SET active_count=MAX(0,active_count-1) WHERE user_id=?', (item['assigned_to'],))
        if new_status == 'RESOLVED':
            NotificationService.notify(item['reporter_id'], incident_id, 'Incident resolved', f'{tag} has been resolved. Please confirm the fix and leave feedback.')
        if new_status == 'IN_PROGRESS' and old == 'RESOLVED' and item['assigned_to']:
            NotificationService.notify(item['assigned_to'], incident_id, 'Incident reopened', f'{tag} was reopened — the issue is not fixed yet.')
        if new_status == 'CLOSED':
            NotificationService.notify(item['reporter_id'], incident_id, 'Incident closed', f'{tag} has been closed.')

class IncidentFactory:
    """Factory: validates input, classifies, prioritizes, applies SLA and persists a new incident."""
    @staticmethod
    def _score(form, field, low=1, high=5, default=3):
        raw = form.get(field, default)
        try: value = int(raw)
        except (TypeError, ValueError): raise ValidationError(f'{field.replace("_", " ").title()} must be a whole number.')
        if not low <= value <= high: raise ValidationError(f'{field.replace("_", " ").title()} must be between {low} and {high}.')
        return value

    @classmethod
    def create(cls, form, reporter_id):
        title = (form.get('title') or '').strip(); desc = (form.get('description') or '').strip()
        if len(title) < 5 or len(desc) < 10: raise ValidationError('Enter a title (5+ chars) and description (10+ chars).')
        category = form.get('category') or IntelligenceService.suggest_category(title + ' ' + desc)
        if category not in CATEGORIES: raise ValidationError('Unknown category.')
        service = form.get('service') or 'General IT'
        if service not in SERVICES: raise ValidationError('Service not found.')
        impact = cls._score(form, 'impact'); urgency = cls._score(form, 'urgency'); criticality = cls._score(form, 'criticality')
        affected = cls._score(form, 'affected_users', 1, 100000, 1)
        strategy_key = Settings.get('priority_strategy', 'weighted')
        priority = PRIORITY_STRATEGIES.get(strategy_key, WeightedPriorityStrategy)().calculate(impact, urgency, criticality, affected)
        created = datetime.now(); response_due, resolution_due = SLAService.deadlines(priority, created)
        iid = Repository.run('''INSERT INTO incidents(reporter_id,title,description,category,service,impact,urgency,criticality,affected_users,priority,status,created_at,updated_at,response_due,resolution_due)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (reporter_id, title, desc, category, service, impact, urgency, criticality, affected, priority, 'NEW',
          created.isoformat(timespec='seconds'), created.isoformat(timespec='seconds'), response_due.isoformat(timespec='seconds'), resolution_due.isoformat(timespec='seconds')))
        auto = '' if form.get('category') else ' (auto-detected)'
        AuditService.record(iid, reporter_id, 'CREATED', f'Incident created on service {service}')
        AuditService.record(iid, None, 'CLASSIFIED', f'Category {category}{auto}')
        AuditService.record(iid, None, 'PRIORITIZED', f'Priority {priority} using {strategy_key} strategy')
        NotificationService.notify_admins(iid, 'New incident', f'INC-{iid:04d} [{priority}]: {title}')
        return iid, priority

# ---------- Strategy pattern: priority ----------
class PriorityStrategy:
    def calculate(self, impact, urgency, criticality, affected_users): raise NotImplementedError
class WeightedPriorityStrategy(PriorityStrategy):
    def calculate(self, impact, urgency, criticality, affected_users):
        score = impact * urgency * criticality + min(affected_users, 100) / 25
        return 'P1' if score >= 20 else 'P2' if score >= 12 else 'P3' if score >= 6 else 'P4'
class ConservativePriorityStrategy(PriorityStrategy):
    def calculate(self, impact, urgency, criticality, affected_users):
        score = impact + urgency + criticality + (2 if affected_users >= 50 else 0)
        return 'P1' if score >= 11 else 'P2' if score >= 8 else 'P3' if score >= 5 else 'P4'
PRIORITY_STRATEGIES = {'weighted': WeightedPriorityStrategy, 'conservative': ConservativePriorityStrategy}

# ---------- Strategy pattern: technician assignment ----------
class AssignmentStrategy:
    MAX_ACTIVE = 5   # workload cap per technician
    label = 'Base'
    def choose(self, category): raise NotImplementedError
    @classmethod
    def available(cls):
        return Repository.all('''SELECT u.id,u.name,t.skills,t.active_count,t.experience FROM users u
          JOIN technicians t ON t.user_id=u.id WHERE u.active=1 AND t.active_count<? ORDER BY u.id''', (cls.MAX_ACTIVE,))
class SkillBasedAssignment(AssignmentStrategy):
    label = 'Skill + workload'
    def choose(self, category):
        rows = self.available()
        if not rows: return None
        matching = [r for r in rows if category.lower() in r['skills'].lower() or 'general' in r['skills'].lower()]
        pool = matching or list(rows)
        return min(pool, key=lambda r: (r['active_count'], -r['experience']))['id']
class LoadBasedAssignment(AssignmentStrategy):
    label = 'Least loaded'
    def choose(self, category):
        rows = self.available()
        return min(rows, key=lambda r: (r['active_count'], r['id']))['id'] if rows else None
class RoundRobinAssignment(AssignmentStrategy):
    label = 'Round robin'
    def choose(self, category):
        rows = self.available()
        if not rows: return None
        last = Settings.get('last_technician')
        ids = [r['id'] for r in rows]
        nxt = next((x for x in ids if last is None or x > int(last)), ids[0])
        Settings.set('last_technician', nxt)
        return nxt
ASSIGNMENT_STRATEGIES = {'skill': SkillBasedAssignment, 'load': LoadBasedAssignment, 'round': RoundRobinAssignment}

class AssignmentService:
    @staticmethod
    def assign(iid, strategy_key, actor):
        item = Repository.one('SELECT * FROM incidents WHERE id=?', (iid,))
        if not item: raise ITSMError('Incident not found')
        if item['status'] != 'NEW': raise InvalidTransitionError('Only NEW incidents can be assigned.')
        strategy = ASSIGNMENT_STRATEGIES.get(strategy_key, SkillBasedAssignment)()
        tech = strategy.choose(item['category'])
        if not tech: raise TechnicianUnavailableError('No active technician has spare capacity (workload limit reached).')
        Incident.transition(iid, 'CLASSIFIED', actor)
        Repository.run('UPDATE incidents SET assigned_to=? WHERE id=?', (tech, iid))
        Incident.transition(iid, 'ASSIGNED', actor)
        Repository.run('UPDATE technicians SET active_count=active_count+1 WHERE user_id=?', (tech,))
        name = Repository.one('SELECT name FROM users WHERE id=?', (tech,))['name']
        AuditService.record(iid, actor, 'ASSIGNED', f'Assigned to {name} using {strategy.label} strategy')
        NotificationService.notify(tech, iid, 'Incident assigned', f'INC-{iid:04d} [{item["priority"]}] has been assigned to you.')
        NotificationService.notify(item['reporter_id'], iid, 'Technician assigned', f'{name} is now working on INC-{iid:04d}.')
        return name

# ---------- SLA ----------
class SLAService:
    TARGETS = {'P1': (5, 30), 'P2': (15, 120), 'P3': (60, 480), 'P4': (240, 1440)}   # defaults from the brief
    @classmethod
    def targets(cls, priority):
        try:
            row = Repository.one('SELECT response_min,resolution_min FROM sla_policies WHERE priority=?', (priority,))
            if row: return row['response_min'], row['resolution_min']
        except sqlite3.Error: pass
        if priority not in cls.TARGETS: raise SLAConfigurationError(f'No SLA policy configured for {priority}')
        return cls.TARGETS[priority]
    @classmethod
    def deadlines(cls, priority, created):
        response, resolution = cls.targets(priority)
        return (created + timedelta(minutes=response), created + timedelta(minutes=resolution))
    @classmethod
    def status(cls, incident, at=None):
        at = at or datetime.now()
        deadline = datetime.fromisoformat(incident['resolution_due'])
        keys = incident.keys()
        if incident['status'] in ('RESOLVED', 'VERIFIED', 'CLOSED'):
            done = incident['resolved_at'] if 'resolved_at' in keys and incident['resolved_at'] else None
            return 'BREACHED' if done and datetime.fromisoformat(done) > deadline else 'MET'
        acknowledged = 'acknowledged_at' in keys and incident['acknowledged_at']
        if not acknowledged and 'response_due' in keys and incident['response_due'] and at > datetime.fromisoformat(incident['response_due']):
            return 'BREACHED'
        remaining = (deadline - at).total_seconds() / 60
        created = datetime.fromisoformat(incident['created_at'])
        window = max(1, (deadline - created).total_seconds() / 60)
        return 'BREACHED' if remaining < 0 else 'AT RISK' if remaining <= max(5, min(30, window * 0.25)) else 'ON TRACK'

class SLAMonitor:
    """Periodic SLA check: warns before deadlines, records violations and escalates unacknowledged incidents."""
    ESCALATION_LEVELS = {1: 'Team Lead', 2: 'IT Manager'}
    _last_run = 0

    @staticmethod
    def _record_once(incident_id, kind, detail):
        try:
            Repository.run('INSERT INTO sla_events(incident_id,kind,detail,created_at) VALUES(?,?,?,?)', (incident_id, kind, detail, now_iso()))
            return True
        except sqlite3.IntegrityError:
            return False   # already recorded

    @classmethod
    def run(cls, at=None):
        at = at or datetime.now(); events = 0
        rows = Repository.all(f"SELECT * FROM incidents WHERE status IN {OPEN_STATUSES}")
        for i in rows:
            tag = f"INC-{i['id']:04d}"; created = datetime.fromisoformat(i['created_at'])
            response_due = datetime.fromisoformat(i['response_due']); resolution_due = datetime.fromisoformat(i['resolution_due'])
            status = SLAService.status(i, at)
            if status == 'AT RISK' and cls._record_once(i['id'], 'WARNING', 'Resolution deadline approaching'):
                events += 1
                AuditService.record(i['id'], None, 'SLA_WARNING', f'Resolution due at {i["resolution_due"][11:16]}')
                if i['assigned_to']: NotificationService.notify(i['assigned_to'], i['id'], 'SLA approaching', f'{tag} must be resolved by {i["resolution_due"][11:16]}.')
                NotificationService.notify_admins(i['id'], 'SLA approaching', f'{tag} is at risk of breaching its SLA.')
            if at > resolution_due and cls._record_once(i['id'], 'RESOLUTION_BREACH', 'Resolution target missed'):
                events += 1
                AuditService.record(i['id'], None, 'SLA_VIOLATED', 'Resolution target missed')
                if i['assigned_to']: NotificationService.notify(i['assigned_to'], i['id'], 'SLA violated', f'{tag} has breached its resolution SLA.')
                NotificationService.notify_admins(i['id'], 'SLA violated', f'{tag} has breached its resolution SLA.')
            if not i['acknowledged_at'] and at > response_due:
                if cls._record_once(i['id'], 'RESPONSE_BREACH', 'Not acknowledged within response target'):
                    events += 1
                    AuditService.record(i['id'], None, 'SLA_VIOLATED', 'Response target missed (not acknowledged)')
                window = response_due - created
                level = 2 if at > created + 3 * window else 1
                if (i['escalation_level'] or 0) < level:
                    for lvl in range((i['escalation_level'] or 0) + 1, level + 1):
                        cls._record_once(i['id'], f'ESCALATED_L{lvl}', cls.ESCALATION_LEVELS[lvl])
                        AuditService.record(i['id'], None, 'ESCALATED', f'Escalated to {cls.ESCALATION_LEVELS[lvl]} (level {lvl}) — not acknowledged in time')
                        NotificationService.notify_admins(i['id'], f'Escalation L{lvl}: {cls.ESCALATION_LEVELS[lvl]}', f'{tag} [{i["priority"]}] was not acknowledged in time.')
                        if i['assigned_to']: NotificationService.notify(i['assigned_to'], i['id'], 'Escalation', f'{tag} has been escalated to the {cls.ESCALATION_LEVELS[lvl]}. Please acknowledge now.')
                        events += 1
                    Repository.run('UPDATE incidents SET escalation_level=? WHERE id=?', (level, i['id']))
        cls._last_run = time.time()
        return events

    @classmethod
    def run_if_due(cls, every_seconds=30):
        if time.time() - cls._last_run >= every_seconds: cls.run()

# ---------- Intelligent features ----------
class IntelligenceService:
    KEYWORDS = {'NETWORK': ['wifi', 'internet', 'vpn', 'network', 'lan'], 'HARDWARE': ['laptop', 'keyboard', 'screen', 'printer', 'hardware', 'projector'],
      'AUTHENTICATION': ['login', 'password', 'otp', 'authentication', 'account'], 'DATABASE': ['database', 'sql', 'query', 'db'],
      'APPLICATION': ['app', 'application', 'software', 'crash', 'payment', 'portal'], 'CLOUD': ['cloud', 'server', 'aws'],
      'SECURITY': ['virus', 'malware', 'phishing', 'hacked', 'security', 'suspicious'], 'STORAGE': ['storage', 'disk', 'drive', 'backup', 'quota']}
    @classmethod
    def suggest_category(cls, text):
        value = text.lower()
        scores = {cat: sum(word in value for word in words) for cat, words in cls.KEYWORDS.items()}
        return max(scores, key=scores.get) if max(scores.values(), default=0) else 'APPLICATION'
    @staticmethod
    def tokens(text): return set(re.findall(r'[a-z]{4,}', text.lower()))
    @classmethod
    def similarity(cls, a, b):
        x, y = cls.tokens(a), cls.tokens(b)
        return len(x & y) / max(1, len(x | y))
    @classmethod
    def possible_duplicates(cls, title, description):
        text = title + ' ' + description; matches = []
        for row in Repository.all("SELECT id,title,description FROM incidents WHERE status NOT IN ('CLOSED','VERIFIED') ORDER BY id DESC LIMIT 100"):
            score = cls.similarity(text, row['title'] + ' ' + row['description'])
            if score >= .28: matches.append((row['id'], round(score, 2)))
        return matches[:5]
    @classmethod
    def suggest_resolutions(cls, title, description, category, exclude_id=None):
        """Suggested resolution: reuse fixes from similar resolved/closed incidents."""
        text = title + ' ' + description; found = []
        for row in Repository.all("SELECT id,title,description,category,resolution FROM incidents WHERE status IN ('RESOLVED','VERIFIED','CLOSED') AND resolution IS NOT NULL AND resolution<>''"):
            if row['id'] == exclude_id: continue
            score = cls.similarity(text, row['title'] + ' ' + row['description']) + (0.15 if row['category'] == category else 0)
            if score >= 0.25: found.append({'id': row['id'], 'title': row['title'], 'resolution': row['resolution'], 'score': round(min(score, 1), 2)})
        return sorted(found, key=lambda r: -r['score'])[:3]
    @staticmethod
    def correlated_groups(window_minutes=60, threshold=3, at=None):
        """Incident correlation: several open incidents on the same service in a short window hint at a common cause."""
        since = ((at or datetime.now()) - timedelta(minutes=window_minutes)).isoformat(timespec='seconds')
        return Repository.all(f'''SELECT service, COUNT(*) n, GROUP_CONCAT(id) ids FROM incidents
          WHERE status IN {OPEN_STATUSES} AND created_at>=? AND service<>'General IT' GROUP BY service HAVING COUNT(*)>=? ORDER BY n DESC''', (since, threshold))

# ---------- Schema / seed ----------
def _add_column(db, table, column, ddl):
    if column not in [r[1] for r in db.execute(f'PRAGMA table_info({table})')]:
        db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')

def init_db():
    with Database.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL,active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS technicians(user_id INTEGER PRIMARY KEY REFERENCES users(id),skills TEXT,experience INTEGER DEFAULT 1,active_count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY AUTOINCREMENT,reporter_id INTEGER REFERENCES users(id),title TEXT,description TEXT,category TEXT,service TEXT,impact INTEGER,urgency INTEGER,criticality INTEGER,affected_users INTEGER,priority TEXT,status TEXT,assigned_to INTEGER REFERENCES users(id),created_at TEXT,updated_at TEXT,response_due TEXT,resolution_due TEXT,resolution TEXT);
        CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,incident_id INTEGER REFERENCES incidents(id),user_id INTEGER REFERENCES users(id),action TEXT,description TEXT,previous_state TEXT,new_state TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER REFERENCES users(id),incident_id INTEGER REFERENCES incidents(id),title TEXT,message TEXT,created_at TEXT,read INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS email_outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,to_email TEXT,subject TEXT,body TEXT,created_at TEXT,status TEXT DEFAULT 'SIMULATED');
        CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY AUTOINCREMENT,incident_id INTEGER REFERENCES incidents(id),user_id INTEGER REFERENCES users(id),kind TEXT,body TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS feedback(incident_id INTEGER PRIMARY KEY REFERENCES incidents(id),user_id INTEGER REFERENCES users(id),rating INTEGER,comment TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS sla_events(id INTEGER PRIMARY KEY AUTOINCREMENT,incident_id INTEGER REFERENCES incidents(id),kind TEXT,detail TEXT,created_at TEXT,UNIQUE(incident_id,kind));
        CREATE TABLE IF NOT EXISTS sla_policies(priority TEXT PRIMARY KEY,response_min INTEGER NOT NULL,resolution_min INTEGER NOT NULL);
        ''')
        for col, ddl in [('acknowledged_at', 'TEXT'), ('resolved_at', 'TEXT'), ('closed_at', 'TEXT'), ('escalation_level', 'INTEGER DEFAULT 0')]:
            _add_column(db, 'incidents', col, ddl)
        for p, (r, d) in SLAService.TARGETS.items():
            db.execute('INSERT OR IGNORE INTO sla_policies(priority,response_min,resolution_min) VALUES(?,?,?)', (p, r, d))
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('assignment_strategy','skill')")
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('priority_strategy','weighted')")
        fresh = db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
        if fresh:
            users = [('Admin', 'bitsadmin@itsm.demo', 'BITSadmin123', 'ADMIN'), ('Aarav Employee', 'bitsemployee@itsm.demo', 'BITSuser123', 'EMPLOYEE'), ('Neha Network', 'bitsneha@itsm.demo', 'BITStech123', 'TECHNICIAN'), ('Rohan Apps', 'bitsrohan@itsm.demo', 'BITStech123', 'TECHNICIAN'), ('Isha Support', 'bitsisha@itsm.demo', 'BITStech123', 'TECHNICIAN')]
            for n, e, p, r in users: db.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', (n, e, generate_password_hash(p), r))
            for uid, skills, exp in [(3, 'NETWORK, CLOUD', 5), (4, 'APPLICATION, DATABASE', 4), (5, 'GENERAL, HARDWARE, AUTHENTICATION', 3)]: db.execute('INSERT INTO technicians(user_id,skills,experience) VALUES(?,?,?)', (uid, skills, exp))
            db.commit()
        # Migrate databases seeded with the old demo logins to the BITS-prefixed ones
        for old_e, new_e, pw in [('admin@itsm.demo', 'bitsadmin@itsm.demo', 'BITSadmin123'), ('employee@itsm.demo', 'bitsemployee@itsm.demo', 'BITSuser123'), ('neha@itsm.demo', 'bitsneha@itsm.demo', 'BITStech123'), ('rohan@itsm.demo', 'bitsrohan@itsm.demo', 'BITStech123'), ('isha@itsm.demo', 'bitsisha@itsm.demo', 'BITStech123')]:
            if db.execute('SELECT 1 FROM users WHERE email=?', (old_e,)).fetchone() and not db.execute('SELECT 1 FROM users WHERE email=?', (new_e,)).fetchone():
                db.execute('UPDATE users SET email=?,password=? WHERE email=?', (new_e, generate_password_hash(pw), old_e))
        db.commit()
    if fresh and os.environ.get('ITSM_DEMO_DATA', '1') == '1': seed_demo_history()

def seed_demo_history():
    """Sample historical incidents so reports, suggested resolutions and SLA monitoring have data to show."""
    now = datetime.now()
    def at(minutes_ago): return (now - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds')
    samples = [
        # title, description, category, service, impact, urgency, crit, users, priority, status, tech, created(min ago), resolution, rating
        ('Payment gateway timeout during fee payment', 'Students get a timeout error on the fee payment page and the transaction fails', 'APPLICATION', 'Payment', 4, 4, 4, 300, 'P1', 'CLOSED', 4, 4320, 'Restarted the payment service and increased the gateway timeout to 60 seconds', 5),
        ('Campus wifi disconnecting in library', 'Wifi network keeps disconnecting in library, internet not working for students', 'NETWORK', 'Network', 3, 3, 3, 80, 'P1', 'CLOSED', 3, 2880, 'Replaced faulty access point AP-LIB-2 and updated controller firmware', 4),
        ('Unable to login to ERP portal', 'Password reset link fails and account shows locked on the ERP login page', 'AUTHENTICATION', 'Authentication', 2, 3, 2, 1, 'P3', 'CLOSED', 5, 1440, 'Cleared the locked account flag and reset the password from the admin console', 5),
        ('Attendance database query very slow', 'Attendance report query takes several minutes and times out for faculty', 'DATABASE', 'Database', 3, 2, 2, 40, 'P2', 'IN_PROGRESS', 4, 180, None, None),
        ('Campus email not delivering messages', 'Outgoing email from the campus mail server is stuck in the queue since morning', 'CLOUD', 'Email', 4, 4, 3, 500, 'P1', 'NEW', None, 12, None, None),
    ]
    for (title, desc, cat, svc, imp, urg, crit, users, pri, status, tech, ago, resolution, rating) in samples:
        created = now - timedelta(minutes=ago); r_due, d_due = SLAService.deadlines(pri, created)
        ack = at(ago - 3) if status not in ('NEW',) else None
        resolved = at(ago - 25) if status == 'CLOSED' else None
        iid = Repository.run('''INSERT INTO incidents(reporter_id,title,description,category,service,impact,urgency,criticality,affected_users,priority,status,assigned_to,created_at,updated_at,response_due,resolution_due,resolution,acknowledged_at,resolved_at,closed_at)
          VALUES(2,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (title, desc, cat, svc, imp, urg, crit, users, pri, status, tech, created.isoformat(timespec='seconds'), at(max(ago - 30, 0)),
          r_due.isoformat(timespec='seconds'), d_due.isoformat(timespec='seconds'), resolution, ack, resolved, at(ago - 40) if status == 'CLOSED' else None))
        AuditService.record(iid, 2, 'CREATED', f'Incident created on service {svc} (sample data)')
        if tech: AuditService.record(iid, 1, 'ASSIGNED', 'Assigned using Skill + workload strategy (sample data)', 'CLASSIFIED', 'ASSIGNED')
        if status == 'CLOSED': AuditService.record(iid, 1, 'STATUS_CHANGED', 'VERIFIED → CLOSED (sample data)', 'VERIFIED', 'CLOSED')
        if status == 'IN_PROGRESS': Repository.run('UPDATE technicians SET active_count=active_count+1 WHERE user_id=?', (tech,))
        if rating: Repository.run('INSERT INTO feedback(incident_id,user_id,rating,comment,created_at) VALUES(?,?,?,?,?)', (iid, 2, rating, 'Fixed quickly, thanks!', at(ago - 45)))

init_db()

# ---------- Web helpers ----------
def current_user(): return Repository.one('SELECT * FROM users WHERE id=?', (session.get('user_id'),)) if session.get('user_id') else None
def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            if request.path.startswith('/api/'): return jsonify(error='authentication required'), 401
            return redirect(url_for('login'))
        return fn(*a, **kw)
    return wrapper
def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if not u: return redirect(url_for('login'))
            if u['role'] not in allowed: abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco
def visible_incident(iid, u):
    item = Repository.one('SELECT * FROM incidents WHERE id=?', (iid,))
    if not item: abort(404)
    if u['role'] == 'EMPLOYEE' and item['reporter_id'] != u['id']: abort(403)
    if u['role'] == 'TECHNICIAN' and item['assigned_to'] != u['id']: abort(403)
    return item
def scope_sql(u):
    if u['role'] == 'EMPLOYEE': return ' AND reporter_id=?', [u['id']]
    if u['role'] == 'TECHNICIAN': return ' AND assigned_to=?', [u['id']]
    return '', []

@app.before_request
def sla_tick():
    if request.endpoint not in ('static', None): SLAMonitor.run_if_due()
@app.context_processor
def inject():
    u = current_user()
    unread = Repository.one('SELECT COUNT(*) n FROM notifications WHERE user_id=? AND read=0', (u['id'],))['n'] if u else 0
    return {'me': u, 'now': datetime.now(), 'unread': unread, 'sla_of': SLAService.status, 'CATEGORIES': CATEGORIES, 'SERVICES': SERVICES, 'STATUSES': STATUSES}
@app.errorhandler(403)
def forbidden(e): return render_template('error.html', code=403, message='You are not authorized to perform this operation.'), 403
@app.errorhandler(404)
def not_found(e): return render_template('error.html', code=404, message='The page or incident was not found.'), 404

# ---------- Auth ----------
@app.route('/')
def home(): return redirect(url_for('dashboard') if current_user() else url_for('login'))
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = Repository.one('SELECT * FROM users WHERE email=? AND active=1', (request.form.get('email', '').strip().lower(),))
        if u and check_password_hash(u['password'], request.form.get('password', '')):
            session['user_id'] = u['id']; return redirect(url_for('dashboard'))
        flash('Invalid email or password', 'error')
    return render_template('login.html')
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip(); email = request.form.get('email', '').strip().lower(); password = request.form.get('password', '')
        if len(name) < 2 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) or len(password) < 6:
            flash('Enter your name, a valid email and a password of at least 6 characters.', 'error')
        else:
            try:
                uid = Repository.run('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', (name, email, generate_password_hash(password), 'EMPLOYEE'))
                session['user_id'] = uid; flash('Account created. Welcome to the IT service desk!', 'success'); return redirect(url_for('dashboard'))
            except sqlite3.IntegrityError: flash('An account with this email already exists.', 'error')
    return render_template('register.html')
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

# ---------- Dashboard ----------
@app.route('/dashboard')
@login_required
def dashboard():
    u = current_user(); where, args = scope_sql(u)
    rows = Repository.all('SELECT * FROM incidents WHERE 1=1' + where + ' ORDER BY id DESC', args)
    counts = {'OPEN': sum(r['status'] in OPEN_STATUSES for r in rows), 'RESOLVED': sum(r['status'] in ('RESOLVED', 'VERIFIED') for r in rows),
              'CLOSED': sum(r['status'] == 'CLOSED' for r in rows), 'BREACHED': sum(SLAService.status(r) == 'BREACHED' for r in rows if r['status'] in OPEN_STATUSES)}
    extra = {}
    if u['role'] == 'TECHNICIAN':
        extra['workload'] = Repository.one('SELECT active_count FROM technicians WHERE user_id=?', (u['id'],))
        extra['max_active'] = AssignmentStrategy.MAX_ACTIVE
    if u['role'] == 'ADMIN':
        extra['correlations'] = IntelligenceService.correlated_groups()
        extra['at_risk'] = [r for r in rows if r['status'] in OPEN_STATUSES and SLAService.status(r) in ('AT RISK', 'BREACHED')]
    if u['role'] == 'EMPLOYEE':
        extra['awaiting'] = Repository.all('''SELECT DISTINCT c.incident_id FROM comments c JOIN incidents i ON i.id=c.incident_id
          WHERE i.reporter_id=? AND c.kind='INFO_REQUEST' AND c.id=(SELECT MAX(id) FROM comments WHERE incident_id=c.incident_id)''', (u['id'],))
    return render_template('dashboard.html', incidents=rows, counts=counts, **extra)

# ---------- Incidents ----------
@app.route('/incidents/new', methods=['GET', 'POST'])
@roles('EMPLOYEE', 'ADMIN')
def create_incident():
    if request.method == 'POST':
        try:
            title = request.form.get('title', ''); desc = request.form.get('description', '')
            matches = IntelligenceService.possible_duplicates(title, desc)
            iid, priority = IncidentFactory.create(request.form, current_user()['id'])
        except ValidationError as e:
            flash(str(e), 'error'); return render_template('new_incident.html', form=request.form)
        if matches:
            flash('Possible duplicates: ' + ', '.join(f'INC-{x[0]:04d} ({int(x[1] * 100)}%)' for x in matches), 'warning')
            AuditService.record(iid, None, 'DUPLICATE_CHECK', 'Possibly related to ' + ', '.join(f'INC-{x[0]:04d}' for x in matches))
        flash(f'Incident INC-{iid:04d} created with priority {priority}.', 'success'); return redirect(url_for('incident_detail', iid=iid))
    return render_template('new_incident.html', form={})

@app.route('/incidents/<int:iid>')
@login_required
def incident_detail(iid):
    u = current_user(); visible_incident(iid, u)
    item = Repository.one('SELECT i.*,r.name reporter,t.name technician FROM incidents i JOIN users r ON r.id=i.reporter_id LEFT JOIN users t ON t.id=i.assigned_to WHERE i.id=?', (iid,))
    audit = Repository.all('SELECT a.*,u.name actor FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id WHERE incident_id=? ORDER BY a.id DESC', (iid,))
    comments = Repository.all('SELECT c.*,u.name author,u.role FROM comments c JOIN users u ON u.id=c.user_id WHERE incident_id=? ORDER BY c.id', (iid,))
    if u['role'] == 'EMPLOYEE': comments = [c for c in comments if c['kind'] != 'NOTE']
    duplicates = [d for d in IntelligenceService.possible_duplicates(item['title'], item['description']) if d[0] != iid]
    suggestions = IntelligenceService.suggest_resolutions(item['title'], item['description'], item['category'], iid) if u['role'] != 'EMPLOYEE' and item['status'] in OPEN_STATUSES else []
    fb = Repository.one('SELECT * FROM feedback WHERE incident_id=?', (iid,))
    next_states = sorted(Incident.VALID_TRANSITIONS.get(item['status'], set()) - {'RESOLVED', 'CLASSIFIED', 'ASSIGNED'})
    if u['role'] == 'TECHNICIAN': next_states = [s for s in next_states if s not in ('VERIFIED', 'CLOSED')]
    response_minutes = None
    if item['acknowledged_at']:
        response_minutes = round((datetime.fromisoformat(item['acknowledged_at']) - datetime.fromisoformat(item['created_at'])).total_seconds() / 60, 1)
    return render_template('incident.html', incident=item, audit=audit, comments=comments, sla=SLAService.status(item), duplicates=duplicates,
                           suggestions=suggestions, feedback=fb, next_states=next_states, response_minutes=response_minutes,
                           strategies=ASSIGNMENT_STRATEGIES, default_strategy=Settings.get('assignment_strategy', 'skill'))

@app.route('/incidents/<int:iid>/assign', methods=['POST'])
@roles('ADMIN')
def assign(iid):
    try:
        name = AssignmentService.assign(iid, request.form.get('strategy', Settings.get('assignment_strategy', 'skill')), current_user()['id'])
        flash(f'Assigned to {name}.', 'success')
    except ITSMError as e: flash(str(e), 'error')
    return redirect(url_for('incident_detail', iid=iid))

@app.route('/incidents/<int:iid>/transition', methods=['POST'])
@login_required
def transition(iid):
    u = current_user(); item = visible_incident(iid, u); target = request.form.get('status')
    try:
        if u['role'] == 'TECHNICIAN' and target in ('VERIFIED', 'CLOSED'):
            raise UnauthorizedOperationError('Only the reporter or an administrator can verify or close an incident.')
        if u['role'] == 'EMPLOYEE' and not (item['status'] == 'RESOLVED' and target in ('VERIFIED', 'IN_PROGRESS')):
            raise UnauthorizedOperationError('You can only confirm or reopen a resolved incident.')
        Incident.transition(iid, target, u['id'])
        if u['role'] == 'EMPLOYEE': flash('Fix confirmed. Thank you!' if target == 'VERIFIED' else 'Incident reopened. The technician has been notified.', 'success')
        else: flash('Incident status updated.', 'success')
    except ITSMError as e: flash(str(e), 'error')
    return redirect(url_for('incident_detail', iid=iid))

@app.route('/incidents/<int:iid>/resolve', methods=['POST'])
@roles('TECHNICIAN', 'ADMIN')
def resolve(iid):
    item = visible_incident(iid, current_user()); text = request.form.get('resolution', '').strip()
    if item['status'] != 'IN_PROGRESS': flash('Move the incident to IN_PROGRESS before resolving.', 'error')
    elif len(text) < 10: flash('Describe the resolution in at least 10 characters.', 'error')
    else:
        Repository.run('UPDATE incidents SET resolution=? WHERE id=?', (text, iid))
        AuditService.record(iid, current_user()['id'], 'RESOLUTION', text)
        Incident.transition(iid, 'RESOLVED', current_user()['id']); flash('Resolution recorded.', 'success')
    return redirect(url_for('incident_detail', iid=iid))

COMMENT_KINDS = {'EMPLOYEE': {'INFO': 'Additional information'}, 'TECHNICIAN': {'NOTE': 'Investigation note (internal)', 'INFO_REQUEST': 'Request information from reporter'},
                 'ADMIN': {'NOTE': 'Internal note', 'INFO_REQUEST': 'Request information from reporter'}}
@app.route('/incidents/<int:iid>/comment', methods=['POST'])
@login_required
def comment(iid):
    u = current_user(); item = visible_incident(iid, u)
    kind = request.form.get('kind'); body = request.form.get('body', '').strip()
    if kind not in COMMENT_KINDS[u['role']]: flash('This type of update is not allowed for your role.', 'error')
    elif len(body) < 3: flash('Write a message before posting.', 'error')
    elif item['status'] == 'CLOSED': flash('Closed incidents cannot be updated.', 'error')
    else:
        Repository.run('INSERT INTO comments(incident_id,user_id,kind,body,created_at) VALUES(?,?,?,?,?)', (iid, u['id'], kind, body, now_iso()))
        AuditService.record(iid, u['id'], {'INFO': 'INFO_ADDED', 'NOTE': 'NOTE_ADDED', 'INFO_REQUEST': 'INFO_REQUESTED'}[kind], body[:120])
        tag = f'INC-{iid:04d}'
        if kind == 'INFO_REQUEST': NotificationService.notify(item['reporter_id'], iid, 'Information requested', f'Support needs more details on {tag}: {body[:80]}')
        if kind == 'INFO':
            if item['assigned_to']: NotificationService.notify(item['assigned_to'], iid, 'Reporter added information', f'{tag}: {body[:80]}')
            else: NotificationService.notify_admins(iid, 'Reporter added information', f'{tag}: {body[:80]}')
        flash('Update posted.', 'success')
    return redirect(url_for('incident_detail', iid=iid) + '#activity')

@app.route('/incidents/<int:iid>/feedback', methods=['POST'])
@roles('EMPLOYEE')
def feedback(iid):
    u = current_user(); item = visible_incident(iid, u)
    try: rating = int(request.form.get('rating', 0))
    except ValueError: rating = 0
    if item['status'] not in ('RESOLVED', 'VERIFIED', 'CLOSED'): flash('Feedback can be given after the incident is resolved.', 'error')
    elif not 1 <= rating <= 5: flash('Choose a rating from 1 to 5.', 'error')
    elif Repository.one('SELECT 1 FROM feedback WHERE incident_id=?', (iid,)): flash('Feedback was already submitted for this incident.', 'error')
    else:
        Repository.run('INSERT INTO feedback(incident_id,user_id,rating,comment,created_at) VALUES(?,?,?,?,?)', (iid, u['id'], rating, request.form.get('comment', '').strip()[:500], now_iso()))
        AuditService.record(iid, u['id'], 'FEEDBACK', f'Rated {rating}/5')
        flash('Thanks for your feedback!', 'success')
    return redirect(url_for('incident_detail', iid=iid))

# ---------- Search & reports ----------
@app.route('/search')
@login_required
def search():
    f = request.args; q = f.get('q', '').strip()
    sql = 'SELECT * FROM incidents WHERE 1=1'; args = []
    if q:
        num = re.sub(r'(?i)^inc-?0*', '', q)
        sql += ' AND (title LIKE ? OR description LIKE ? OR CAST(id AS TEXT)=?)'; args += [f'%{q}%', f'%{q}%', num]
    for col, val in [('status', f.get('status')), ('priority', f.get('priority')), ('category', f.get('category')), ('service', f.get('service')), ('assigned_to', f.get('tech'))]:
        if val: sql += f' AND {col}=?'; args.append(val)
    if f.get('date_from'): sql += ' AND date(created_at)>=date(?)'; args.append(f['date_from'])
    if f.get('date_to'): sql += ' AND date(created_at)<=date(?)'; args.append(f['date_to'])
    u = current_user(); where, extra = scope_sql(u)
    rows = Repository.all(sql + where + ' ORDER BY id DESC', args + extra)
    if f.get('sla'): rows = [r for r in rows if SLAService.status(r) == f['sla']]
    technicians = Repository.all("SELECT u.id,u.name FROM users u WHERE role='TECHNICIAN'") if u['role'] == 'ADMIN' else []
    return render_template('search.html', incidents=rows, technicians=technicians, filters=f)

@app.route('/reports')
@roles('ADMIN')
def reports():
    rows = Repository.all('SELECT * FROM incidents')
    totals = {'total': len(rows), 'open': sum(r['status'] in OPEN_STATUSES for r in rows), 'critical': sum(r['priority'] == 'P1' for r in rows),
              'resolved': sum(r['status'] in ('RESOLVED', 'VERIFIED', 'CLOSED') for r in rows)}
    by_priority = Repository.all('SELECT priority,COUNT(*) n FROM incidents GROUP BY priority ORDER BY priority')
    by_category = Repository.all('SELECT category,COUNT(*) n FROM incidents GROUP BY category ORDER BY n DESC')
    by_service = Repository.all('SELECT service,COUNT(*) n FROM incidents GROUP BY service ORDER BY n DESC')
    workload = Repository.all('''SELECT u.name,t.active_count,t.skills,t.experience,
        (SELECT COUNT(*) FROM incidents i WHERE i.assigned_to=u.id AND i.status IN ('RESOLVED','VERIFIED','CLOSED')) resolved
        FROM technicians t JOIN users u ON u.id=t.user_id''')
    violations = Repository.all('''SELECT e.*,i.title,i.priority FROM sla_events e JOIN incidents i ON i.id=e.incident_id
        WHERE e.kind IN ('RESPONSE_BREACH','RESOLUTION_BREACH') OR e.kind LIKE 'ESCALATED%' ORDER BY e.id DESC LIMIT 20''')
    breached = sum(SLAService.status(r) == 'BREACHED' for r in rows)
    rating = Repository.one('SELECT ROUND(AVG(rating),1) avg, COUNT(*) n FROM feedback')
    acks = [(datetime.fromisoformat(r['acknowledged_at']) - datetime.fromisoformat(r['created_at'])).total_seconds() / 60 for r in rows if r['acknowledged_at']]
    avg_response = round(sum(acks) / len(acks), 1) if acks else None
    return render_template('reports.html', totals=totals, by_priority=by_priority, by_category=by_category, by_service=by_service, workload=workload,
                           violations=violations, breached=breached, rating=rating, avg_response=avg_response, correlations=IntelligenceService.correlated_groups())

@app.route('/notifications')
@login_required
def notifications():
    rows = Repository.all('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC', (current_user()['id'],))
    Repository.run('UPDATE notifications SET read=1 WHERE user_id=?', (current_user()['id'],))
    return render_template('notifications.html', notifications=rows)

# ---------- Administration ----------
@app.route('/admin/email-log')
@roles('ADMIN')
def email_log():
    return render_template('email_log.html', emails=Repository.all('SELECT * FROM email_outbox ORDER BY id DESC LIMIT 100'))

@app.route('/admin/audit')
@roles('ADMIN')
def audit_log():
    rows = Repository.all('SELECT a.*,u.name actor FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 200')
    return render_template('audit.html', rows=rows)

@app.route('/admin/sla-check', methods=['POST'])
@roles('ADMIN')
def sla_check():
    n = SLAMonitor.run(); flash(f'SLA monitor ran: {n} new warning/violation/escalation event(s).', 'success' if n == 0 else 'warning')
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/admin/settings', methods=['GET', 'POST'])
@roles('ADMIN')
def settings_page():
    if request.method == 'POST':
        try:
            if request.form.get('assignment_strategy') not in ASSIGNMENT_STRATEGIES or request.form.get('priority_strategy') not in PRIORITY_STRATEGIES:
                raise ValidationError('Choose a valid strategy.')
            policies = {}
            for p in ['P1', 'P2', 'P3', 'P4']:
                try: r, d = int(request.form[f'{p}_response']), int(request.form[f'{p}_resolution'])
                except (KeyError, ValueError): raise ValidationError(f'{p}: targets must be whole minutes.')
                if r <= 0 or d <= 0 or r >= d: raise ValidationError(f'{p}: response target must be positive and shorter than the resolution target.')
                policies[p] = (r, d)
            Settings.set('assignment_strategy', request.form['assignment_strategy']); Settings.set('priority_strategy', request.form['priority_strategy'])
            for p, (r, d) in policies.items(): Repository.run('UPDATE sla_policies SET response_min=?,resolution_min=? WHERE priority=?', (r, d, p))
            flash('Settings saved. New SLA targets apply to newly created incidents.', 'success')
        except ValidationError as e: flash(str(e), 'error')
        return redirect(url_for('settings_page'))
    return render_template('settings.html', policies=Repository.all('SELECT * FROM sla_policies ORDER BY priority'),
                           assignment=Settings.get('assignment_strategy', 'skill'), priority=Settings.get('priority_strategy', 'weighted'),
                           assignment_options=ASSIGNMENT_STRATEGIES, priority_options=PRIORITY_STRATEGIES, max_active=AssignmentStrategy.MAX_ACTIVE)

@app.route('/admin/users', methods=['GET', 'POST'])
@roles('ADMIN')
def users():
    if request.method == 'POST':
        if request.form.get('action') == 'toggle':
            uid = int(request.form['user_id'])
            if uid == current_user()['id']: flash('You cannot deactivate your own account.', 'error')
            else: Repository.run('UPDATE users SET active=1-active WHERE id=?', (uid,)); flash('Account status updated.', 'success')
            return redirect(url_for('users'))
        name = request.form['name'].strip(); email = request.form['email'].strip().lower(); role = request.form['role']; password = request.form['password']
        if role not in ('EMPLOYEE', 'TECHNICIAN', 'ADMIN') or len(password) < 6: flash('Choose a valid role and password of at least 6 characters.', 'error')
        else:
            try:
                uid = Repository.run('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', (name, email, generate_password_hash(password), role))
                if role == 'TECHNICIAN': Repository.run('INSERT INTO technicians(user_id,skills,experience) VALUES(?,?,?)', (uid, request.form.get('skills') or 'GENERAL', max(1, int(request.form.get('experience') or 1))))
                flash('User created.', 'success')
            except sqlite3.IntegrityError: flash('Email already exists.', 'error')
    rows = Repository.all('SELECT u.*,t.skills,t.active_count,t.experience FROM users u LEFT JOIN technicians t ON t.user_id=u.id ORDER BY u.id')
    return render_template('users.html', users=rows)

# ---------- REST API (session-authenticated, role-scoped, read-only) ----------
def incident_json(r):
    d = {k: r[k] for k in r.keys()}; d['code'] = f"INC-{r['id']:04d}"; d['sla_status'] = SLAService.status(r); return d
@app.route('/api/incidents')
@login_required
def api_incidents():
    where, args = scope_sql(current_user())
    return jsonify([incident_json(r) for r in Repository.all('SELECT * FROM incidents WHERE 1=1' + where + ' ORDER BY id DESC', args)])
@app.route('/api/incidents/<int:iid>')
@login_required
def api_incident(iid):
    return jsonify(incident_json(visible_incident(iid, current_user())))
@app.route('/api/stats')
@login_required
def api_stats():
    if current_user()['role'] != 'ADMIN': return jsonify(error='forbidden'), 403
    rows = Repository.all('SELECT * FROM incidents')
    return jsonify(total=len(rows), open=sum(r['status'] in OPEN_STATUSES for r in rows), breached=sum(SLAService.status(r) == 'BREACHED' for r in rows),
                   by_priority={r['priority']: r['n'] for r in Repository.all('SELECT priority,COUNT(*) n FROM incidents GROUP BY priority')})

if __name__ == '__main__': app.run(debug=True)
