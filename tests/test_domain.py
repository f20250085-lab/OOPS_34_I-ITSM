import unittest
import os, tempfile
os.environ.setdefault("ITSM_DB", os.path.join(tempfile.mkdtemp(), "test.db")); os.environ.setdefault("ITSM_DEMO_DATA", "0")
from datetime import datetime
from app import WeightedPriorityStrategy, ConservativePriorityStrategy, SLAService, Incident, IntelligenceService

class DomainTests(unittest.TestCase):
    def test_weighted_p1(self): self.assertEqual(WeightedPriorityStrategy().calculate(5,5,5,100),'P1')
    def test_weighted_p4(self): self.assertEqual(WeightedPriorityStrategy().calculate(1,1,1,1),'P4')
    def test_weighted_p2(self): self.assertEqual(WeightedPriorityStrategy().calculate(2,2,3,1),'P2')
    def test_weighted_p3(self): self.assertEqual(WeightedPriorityStrategy().calculate(2,2,2,1),'P3')
    def test_users_capped(self): self.assertEqual(WeightedPriorityStrategy().calculate(2,2,2,10000),WeightedPriorityStrategy().calculate(2,2,2,100))
    def test_conservative_p1(self): self.assertEqual(ConservativePriorityStrategy().calculate(5,5,5,1),'P1')
    def test_conservative_p4(self): self.assertEqual(ConservativePriorityStrategy().calculate(1,1,1,1),'P4')
    def test_p1_sla(self):
        r,d=SLAService.deadlines('P1',datetime(2026,1,1,10,0)); self.assertEqual((r-datetime(2026,1,1,10,0)).total_seconds(),300); self.assertEqual((d-datetime(2026,1,1,10,0)).total_seconds(),1800)
    def test_p2_sla(self):
        r,d=SLAService.deadlines('P2',datetime(2026,1,1,10,0)); self.assertEqual((r-datetime(2026,1,1,10,0)).total_seconds(),900); self.assertEqual((d-datetime(2026,1,1,10,0)).total_seconds(),7200)
    def test_p3_sla(self):
        r,d=SLAService.deadlines('P3',datetime(2026,1,1,10,0)); self.assertEqual((r-datetime(2026,1,1,10,0)).total_seconds(),3600); self.assertEqual((d-datetime(2026,1,1,10,0)).total_seconds(),28800)
    def test_p4_sla(self):
        r,d=SLAService.deadlines('P4',datetime(2026,1,1,10,0)); self.assertEqual((r-datetime(2026,1,1,10,0)).total_seconds(),14400); self.assertEqual((d-datetime(2026,1,1,10,0)).total_seconds(),86400)
    def test_category_network(self): self.assertEqual(IntelligenceService.suggest_category('VPN network is down'),'NETWORK')
    def test_category_hardware(self): self.assertEqual(IntelligenceService.suggest_category('Laptop screen is broken'),'HARDWARE')
    def test_category_database(self): self.assertEqual(IntelligenceService.suggest_category('SQL database query failing'),'DATABASE')
    def test_category_default(self): self.assertEqual(IntelligenceService.suggest_category('Something strange happened'),'APPLICATION')
    def test_lifecycle_rejects_new_to_closed(self): self.assertNotIn('CLOSED',Incident.VALID_TRANSITIONS['NEW'])
    def test_lifecycle_resolved_can_reopen(self): self.assertIn('IN_PROGRESS',Incident.VALID_TRANSITIONS['RESOLVED'])

if __name__=='__main__': unittest.main()
