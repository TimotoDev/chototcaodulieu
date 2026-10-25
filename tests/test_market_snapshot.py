import unittest
from market_snapshot import build_snapshot


class SnapshotTests(unittest.TestCase):
    def test_counts_every_record_not_only_first_page_and_marks_missing_dates(self):
        rows=({'brand':'Honda','vehicle_type':None,'region':'Hà Nội','price_vnd':None,'posted_at':None,'observed_at':'2026-10-25T22:00:00.000Z'} for _ in range(5001))
        result=build_snapshot(rows,'2026-10-25T22:01:00.000Z')
        self.assertEqual(result['total'],5001)
        self.assertEqual(result['unknown_posted'],5001)
        self.assertEqual(result['daily'],[{'date':'2026-10-26','count':5001}])
        self.assertEqual(result['priced'],0)
