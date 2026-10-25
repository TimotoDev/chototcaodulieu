import unittest
from moto import mock_aws
from test_market_store import StoreTests
from market_data import normalize_chotot
from market_read import listings_page


@mock_aws
class PaginationTests(StoreTests):
    def test_all_sources_pages_have_no_loss_under_filtering(self):
        expected=set()
        for i in range(12):
            row=normalize_chotot({'list_id':i+1,'price':10000000+i},f'2026-10-25T20:00:{i:02}.000Z')
            if i%2:
                row.update(source='facebook_marketplace',listing_id=f'facebook_marketplace:{i+1}',url=f'https://www.facebook.com/marketplace/item/{i+1}/',title='Motorcycle listing')
            self.store.upsert(row)
            if i>=3:expected.add(row['listing_id'])
        params={'limit':'2','min_price':'10000003'}
        actual=[]
        while True:
            page=listings_page(self.table,params)
            actual.extend(x['listing_id'] for x in page['items'])
            if not page['has_more']:break
            params['cursor']=page['next_cursor']
        self.assertEqual(set(actual),expected)
        self.assertEqual(len(actual),len(expected))

    def test_empty_bounded_page_still_exposes_continuation(self):
        for i in range(6):self.store.upsert(normalize_chotot({'list_id':i+1,'price':100},f'2026-10-25T20:00:{i:02}.000Z'))
        page=listings_page(self.table,{'source':'chotot','brand':'no match','limit':'2'},read_budget=2)
        self.assertEqual(page['items'],[])
        self.assertTrue(page['has_more'])

    def test_cursor_rejects_changed_filters_and_malformed_input(self):
        for i in range(3):self.store.upsert(normalize_chotot({'list_id':i+1},f'2026-10-25T20:00:{i:02}.000Z'))
        page=listings_page(self.table,{'source':'chotot','limit':'1'})
        with self.assertRaises(ValueError):listings_page(self.table,{'source':'facebook_group','cursor':page['next_cursor']})
        with self.assertRaises(ValueError):listings_page(self.table,{'cursor':'garbage'})
        with self.assertRaises(ValueError):listings_page(self.table,{'limit':'-1'})
