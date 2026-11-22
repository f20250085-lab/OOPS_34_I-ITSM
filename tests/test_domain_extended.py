"""Extended domain-rule tests: priority boundaries, classification, text similarity, lifecycle and SLA maths."""
import os, tempfile, unittest
os.environ.setdefault('ITSM_DB', os.path.join(tempfile.mkdtemp(), 'test.db')); os.environ.setdefault('ITSM_DEMO_DATA', '0')
from datetime import datetime, timedelta
from app import (WeightedPriorityStrategy as W, ConservativePriorityStrategy as C, PriorityStrategy, PRIORITY_STRATEGIES, SLAService,
                 Incident, IntelligenceService as AI, ITSMError, ValidationError, InvalidTransitionError, UnauthorizedOperationError,
                 TechnicianUnavailableError, SLAConfigurationError, AssignmentStrategy)

class PriorityBoundaryTests(unittest.TestCase):
    def test_weighted_exactly_at_p1_threshold(self): self.assertEqual(W().calculate(5, 4, 1, 1), 'P1')        # 20.04
    def test_weighted_exactly_at_p2_threshold(self): self.assertEqual(W().calculate(3, 2, 2, 1), 'P2')        # 12.04
    def test_weighted_just_under_p2_is_p3(self): self.assertEqual(W().calculate(5, 2, 1, 1), 'P3')            # 10.04
    def test_weighted_exactly_at_p3_threshold(self): self.assertEqual(W().calculate(2, 3, 1, 1), 'P3')        # 6.04
    def test_weighted_just_under_p3_is_p4(self): self.assertEqual(W().calculate(1, 1, 5, 1), 'P4')            # 5.04
    def test_weighted_affected_users_can_lift_priority(self): self.assertEqual(W().calculate(1, 1, 5, 100), 'P3')   # 5 + 4
    def test_conservative_p1_needs_score_eleven(self):
        self.assertEqual(C().calculate(4, 3, 3, 1), 'P2'); self.assertEqual(C().calculate(4, 4, 3, 1), 'P1')
    def test_conservative_bonus_at_fifty_users(self):
        self.assertEqual(C().calculate(3, 3, 3, 49), 'P2'); self.assertEqual(C().calculate(3, 3, 3, 50), 'P1')
    def test_priority_base_class_is_abstract(self):
        with self.assertRaises(NotImplementedError): PriorityStrategy().calculate(1, 1, 1, 1)
        self.assertTrue(all(issubclass(c, PriorityStrategy) for c in PRIORITY_STRATEGIES.values()))

class ClassificationTests(unittest.TestCase):
    def test_category_authentication(self): self.assertEqual(AI.suggest_category('Cannot login, my password has expired'), 'AUTHENTICATION')
    def test_category_security(self): self.assertEqual(AI.suggest_category('Phishing message and malware detected'), 'SECURITY')
    def test_category_storage(self): self.assertEqual(AI.suggest_category('Disk quota exceeded on the backup drive'), 'STORAGE')
    def test_category_cloud(self): self.assertEqual(AI.suggest_category('AWS server is unreachable'), 'CLOUD')
class TextSimilarityTests(unittest.TestCase):
    def test_similarity_of_identical_text_is_one(self): self.assertEqual(AI.similarity('printer jammed again', 'printer jammed again'), 1.0)
    def test_similarity_of_unrelated_text_is_zero(self): self.assertEqual(AI.similarity('printer jammed', 'payroll unavailable'), 0.0)
    def test_similarity_partial_overlap(self): self.assertEqual(AI.similarity('printer jammed again', 'printer jammed today'), 0.5)

class LifecycleAndErrorTests(unittest.TestCase):
    def test_every_status_has_an_entry_and_closed_is_terminal(self):
        self.assertEqual(len(Incident.VALID_TRANSITIONS), 8); self.assertEqual(Incident.VALID_TRANSITIONS['CLOSED'], set())
    def test_only_resolved_has_more_than_one_exit(self):
        self.assertEqual([s for s, n in Incident.VALID_TRANSITIONS.items() if len(n) > 1], ['RESOLVED'])
    def test_exception_hierarchy(self):
        for exc in (ValidationError, InvalidTransitionError, UnauthorizedOperationError, TechnicianUnavailableError, SLAConfigurationError):
            self.assertTrue(issubclass(exc, ITSMError))
        self.assertTrue(issubclass(ValidationError, ValueError))

class SLAMathTests(unittest.TestCase):
    def test_default_targets_get_longer_as_priority_drops(self):
        res = [SLAService.TARGETS[p] for p in ('P1', 'P2', 'P3', 'P4')]
        self.assertTrue(all(r < d for r, d in res)); self.assertEqual(res, sorted(res))
    def test_unknown_priority_has_no_policy(self):
        with self.assertRaises(SLAConfigurationError): SLAService.targets('P9')
    def _row(self, **kw):
        t = datetime(2026, 1, 1, 9, 0); row = {'status': 'NEW', 'created_at': t.isoformat(), 'response_due': (t + timedelta(minutes=15)).isoformat(),
                                                'resolution_due': (t + timedelta(minutes=120)).isoformat(), 'acknowledged_at': None, 'resolved_at': None}
        row.update(kw); return row, t
    def test_resolved_before_deadline_is_met(self):
        row, t = self._row(status='RESOLVED', resolved_at=datetime(2026, 1, 1, 10, 0).isoformat()); self.assertEqual(SLAService.status(row, t), 'MET')
    def test_resolved_after_deadline_is_breached(self):
        row, t = self._row(status='CLOSED', resolved_at=datetime(2026, 1, 1, 12, 0).isoformat()); self.assertEqual(SLAService.status(row, t), 'BREACHED')
    def test_unacknowledged_past_response_target_is_breached(self):
        row, t = self._row(); self.assertEqual(SLAService.status(row, t + timedelta(minutes=16)), 'BREACHED')
if __name__ == '__main__': unittest.main()
