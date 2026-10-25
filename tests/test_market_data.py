import unittest
from datetime import datetime, timezone
from market_data import normalize_chotot, validate_listing

NOW = '2026-10-25T22:00:00.000Z'


class ContractTests(unittest.TestCase):
    def test_source_scoped_identity_and_unknown_fields(self):
        row = normalize_chotot({'ad': {'list_id': 123, 'subject': 'Xe', 'motorbikebrand': 1}}, NOW)
        self.assertEqual(row['listing_id'], 'chotot:123')
        self.assertIsNone(row['price_vnd'])
        self.assertIsNone(row['posted_at'])
        self.assertEqual(row['brand'], 'Honda')
        self.assertNotIn('seller', row)

    def test_external_url_cannot_cross_source(self):
        row = normalize_chotot({'list_id': 1}, NOW)
        row.update(source='facebook_marketplace', listing_id='facebook_marketplace:1')
        with self.assertRaises(ValueError):
            validate_listing(row)

    def test_negative_price_is_unknown(self):
        row = normalize_chotot({'list_id': 1, 'price': -1}, NOW)
        self.assertIsNone(row['price_vnd'])

    def test_normalization_excludes_raw_identity_and_control_characters(self):
        row = normalize_chotot({'ad': {'list_id': 1, 'account_name': 'PRIVATE', '_raw_json': 'secret', 'subject': 'Xe\x00 máy'}}, NOW)
        self.assertNotIn('PRIVATE', str(row))
        self.assertNotIn('secret', str(row))
        self.assertNotIn('\x00', row['title'])

    def test_facebook_title_cannot_include_free_text(self):
        row = normalize_chotot({'list_id': 1}, NOW)
        row.update(source='facebook_marketplace', listing_id='facebook_marketplace:1', url='https://www.facebook.com/marketplace/item/1/', title='Call 0912345678')
        with self.assertRaises(ValueError):
            validate_listing(row)


if __name__ == '__main__':
    unittest.main()
