"""Portable policy inputs retain the accepted Apollo defaults."""
import unittest
from swarmtenderr_policy import admission, space_planner as space

GIB = 1024**3


class SharedPolicyTests(unittest.TestCase):
    def test_default_capacity_matches_accepted_reserves(self):
        rows = {str(i): {'hash': str(i), 'state': 'queuedDL', 'progress': .1, 'amount_left':10*GIB,
                        'size': 20*GIB, 'completed': GIB, 'priority':i, 'num_seeds':10} for i in range(10)}
        proposed = admission.queue_plan(rows, rows, {}, 1000, max_downloads=10)
        plan = space.plan(rows, rows, {}, 10, proposed, 80*GIB, 200*GIB, 1000*GIB, 500*GIB, 1000)
        self.assertEqual(plan['ssd_budget_bytes'],25*GIB)
        self.assertEqual(len(plan['selected']),2)
        self.assertEqual(plan['reserve_bytes'],50*GIB)
        self.assertTrue(plan['library_capacity_applicable'])

    def test_standalone_without_import_destination_does_not_invent_capacity(self):
        rows = {'a': {'hash':'a','state':'queuedDL','progress':.1,'amount_left':10*GIB,'size':20*GIB,'priority':1,'num_seeds':10}}
        proposed = admission.queue_plan(rows, rows, {}, 1000)
        plan = space.plan(rows, rows, {}, 3, proposed, 50*GIB, 0, None, 500*GIB, 1000, reserve=10*GIB)
        self.assertEqual(plan['ssd_budget_bytes'],35*GIB)
        self.assertIsNone(plan['library_budget_bytes'])
        self.assertFalse(plan['library_capacity_applicable'])
        self.assertEqual(plan['selected'],['a'])
