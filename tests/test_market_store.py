import os
import unittest
from unittest.mock import patch
import boto3
from moto import mock_aws
from market_data import normalize_chotot
from market_store import MarketStore, batch_write_with_retry, CollectionAttempt


@mock_aws
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.ddb = boto3.resource('dynamodb', region_name='ap-southeast-1')
        self.table = self.ddb.create_table(TableName='market', KeySchema=[{'AttributeName':'listing_id','KeyType':'HASH'}], AttributeDefinitions=[{'AttributeName':'listing_id','AttributeType':'S'},{'AttributeName':'source','AttributeType':'S'},{'AttributeName':'source_order','AttributeType':'S'}], GlobalSecondaryIndexes=[{'IndexName':'source-order-index','KeySchema':[{'AttributeName':'source','KeyType':'HASH'},{'AttributeName':'source_order','KeyType':'RANGE'}],'Projection':{'ProjectionType':'ALL'}}], BillingMode='PAY_PER_REQUEST')
        self.meta = self.ddb.create_table(TableName='meta', KeySchema=[{'AttributeName':'key','KeyType':'HASH'}], AttributeDefinitions=[{'AttributeName':'key','AttributeType':'S'}], BillingMode='PAY_PER_REQUEST')
        self.store = MarketStore(self.table, self.meta)

    def test_upsert_preserves_order_and_rejects_stale_replay(self):
        a=normalize_chotot({'list_id':1,'price':10000000},'2026-10-25T20:00:00.000Z')
        self.store.upsert(a)
        b={**a,'price_vnd':12000000,'observed_at':'2026-10-25T21:00:00.000Z'}
        self.store.upsert(b)
        self.store.upsert(a)
        row=self.table.get_item(Key={'listing_id':a['listing_id']})['Item']
        self.assertEqual(row['price_vnd'],12000000)
        self.assertEqual(row['first_seen_at'],a['observed_at'])
        self.assertEqual(row['last_seen_at'],b['observed_at'])
        self.assertEqual(row['source_order'],a['observed_at']+'#chotot:1')

    def test_run_saved_count_is_not_observed_count(self):
        run={'run_id':'abc','source':'facebook_marketplace','mode':'public-only','started_at':'2026-10-25T20:00:00.000Z','finished_at':'2026-10-25T20:00:01.000Z','status':'login_required','observed':0,'saved':0,'rejected':0,'reason':'login_wall'}
        self.store.save_run(run)
        self.assertEqual(self.meta.get_item(Key={'key':'status#facebook_marketplace'})['Item']['status'],'login_required')
        with self.assertRaises(ValueError):
            self.store.save_run({**run,'status':'success'})

    def test_failed_and_empty_attempts_replace_previous_source_health(self):
        with self.assertRaises(RuntimeError):
            with CollectionAttempt('chotot','daily',store=self.store):
                raise RuntimeError('upstream unavailable')
        row=self.meta.get_item(Key={'key':'status#chotot'})['Item']
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['saved'],0)
        with CollectionAttempt('chotot','daily',store=self.store):
            pass
        row=self.meta.get_item(Key={'key':'status#chotot'})['Item']
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['reason'],'no_records')


class RetryTests(unittest.TestCase):
    def test_retries_unprocessed_records_and_returns_only_durable_count(self):
        class Client:
            calls=0
            def batch_write_item(self,RequestItems):
                self.calls+=1
                return {'UnprocessedItems':RequestItems if self.calls<3 else {}}
        client=Client()
        self.assertEqual(batch_write_with_retry(client,'raw',[{'PutRequest':{'Item':{'id':{'N':'1'}}}}],sleep=lambda _:None),1)
        self.assertEqual(client.calls,3)

    def test_exhausted_unprocessed_writes_raise(self):
        class Client:
            def batch_write_item(self,RequestItems): return {'UnprocessedItems':RequestItems}
        with self.assertRaises(RuntimeError):
            batch_write_with_retry(Client(),'raw',[{'PutRequest':{'Item':{'id':{'N':'1'}}}}],sleep=lambda _:None,max_attempts=2)
