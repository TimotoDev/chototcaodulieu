import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import boto3
from moto import mock_aws
from tools.market_backfill import main


@mock_aws
class BackfillTests(unittest.TestCase):
    def test_segments_import_all_rows_and_complete_checkpoint_repairs_metadata(self):
        ddb=boto3.resource('dynamodb',region_name='ap-southeast-1')
        raw=ddb.create_table(TableName='chotot-xe-may',KeySchema=[{'AttributeName':'list_id','KeyType':'HASH'}],AttributeDefinitions=[{'AttributeName':'list_id','AttributeType':'N'}],BillingMode='PAY_PER_REQUEST')
        for i in range(1,15):raw.put_item(Item={'list_id':i,'subject':'Xe','price':10000000})
        out=ddb.create_table(TableName='normalized',KeySchema=[{'AttributeName':'listing_id','KeyType':'HASH'}],AttributeDefinitions=[{'AttributeName':'listing_id','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
        meta=ddb.create_table(TableName='metadata',KeySchema=[{'AttributeName':'key','KeyType':'HASH'}],AttributeDefinitions=[{'AttributeName':'key','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'MARKET_LISTINGS_TABLE':'normalized','MARKET_METADATA_TABLE':'metadata','AWS_REGION':'ap-southeast-1','AWS_DEFAULT_REGION':'ap-southeast-1'}),patch('boto3.setup_default_session'),contextlib.redirect_stdout(io.StringIO()):
            path=Path(td)/'checkpoint.json'
            with patch.object(sys,'argv',['backfill','--checkpoint',str(path),'--segments','2']):
                main()
                self.assertEqual(out.scan(Select='COUNT')['Count'],14)
                self.assertTrue(json.loads(path.read_text())['complete'])
                meta.delete_item(Key={'key':'backfill#chotot'})
                main()
            self.assertTrue(meta.get_item(Key={'key':'backfill#chotot'}).get('Item',{}).get('complete'))
