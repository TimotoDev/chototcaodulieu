"""Durable normalized listing/run writes; never count unprocessed writes as saved."""
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
import boto3
from botocore.exceptions import ClientError
from market_data import utc_now, validate_listing

STATUSES = {'running', 'success', 'partial', 'login_required', 'blocked', 'failed'}


def batch_write_with_retry(client, table_name, requests, max_attempts=6, sleep=time.sleep):
    pending = requests
    for attempt in range(max_attempts):
        response = client.batch_write_item(RequestItems={table_name: pending})
        pending = response.get('UnprocessedItems', {}).get(table_name, [])
        if not pending:
            return len(requests)
        sleep(min(5, .1 * 2 ** attempt) + random.random() * .05)
    raise RuntimeError(f'{len(pending)} DynamoDB writes unprocessed after {max_attempts} attempts')


class MarketStore:
    def __init__(self, listings=None, metadata=None):
        if listings is None or metadata is None:
            ddb = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION', 'ap-southeast-1'))
            listings = ddb.Table(os.environ['MARKET_LISTINGS_TABLE'])
            metadata = ddb.Table(os.environ['MARKET_METADATA_TABLE'])
        self.listings = listings
        self.metadata = metadata

    def upsert(self, row):
        validate_listing(row)
        names = {}
        values = {}
        updates = []
        for i, (key, value) in enumerate(row.items()):
            if key == 'listing_id':
                continue
            names[f'#f{i}'] = key
            values[f':v{i}'] = value
            updates.append(f'#f{i} = :v{i}')
        names.update({'#first': 'first_seen_at', '#last': 'last_seen_at', '#order': 'source_order'})
        values.update({':seen': row['observed_at'], ':order': row['observed_at'] + '#' + row['listing_id']})
        updates.extend(['#first = if_not_exists(#first, :seen)', '#order = if_not_exists(#order, :order)', '#last = :seen'])
        try:
            self.listings.update_item(Key={'listing_id': row['listing_id']}, UpdateExpression='SET ' + ', '.join(updates), ExpressionAttributeNames=names, ExpressionAttributeValues=values, ConditionExpression='attribute_not_exists(#last) OR #last <= :seen')
            return True
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
                return False
            raise

    def save_run(self, run):
        from market_data import SOURCES
        if run.get('source') not in SOURCES or run.get('status') not in STATUSES:
            raise ValueError('Invalid run source/status')
        for field in ('observed', 'saved', 'rejected'):
            if not isinstance(run.get(field), int) or run[field] < 0:
                raise ValueError('Invalid run counts')
        if run['saved'] > run['observed'] or (run['status'] == 'success' and not run['saved']):
            raise ValueError('Success requires durable records')
        allowed = {'run_id','source','mode','started_at','finished_at','status','observed','saved','rejected','reason','archive_key'}
        if set(run) - allowed:
            raise ValueError('Unexpected run fields')
        item = {**run, 'key': f"run#{run['source']}#{run['run_id']}", 'expires_at': int(time.time()) + 90 * 86400}
        self.metadata.put_item(Item=item)
        status = {**item, 'key': f"status#{run['source']}"}
        previous = self.metadata.get_item(Key={'key': status['key']}, ConsistentRead=True).get('Item', {})
        if run['status'] == 'success':
            status['last_success_at'] = run.get('finished_at') or utc_now()
        elif previous.get('last_success_at'):
            status['last_success_at'] = previous['last_success_at']
        try:
            self.metadata.put_item(Item=status, ConditionExpression='attribute_not_exists(started_at) OR started_at <= :started', ExpressionAttributeValues={':started': run['started_at']})
        except ClientError as error:
            if error.response['Error']['Code'] != 'ConditionalCheckFailedException':
                raise

    def archive(self, source, run_id, rows):
        bucket = os.environ.get('MARKET_ARCHIVE_BUCKET')
        if not bucket:
            return None
        for row in rows:
            validate_listing(row)
        key = f'{source}/{utc_now()[:10]}/{run_id}.jsonl'
        body = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
        boto3.client('s3').put_object(Bucket=bucket, Key=key, Body=body.encode(), ContentType='application/x-ndjson', ServerSideEncryption='AES256')
        return key


def mirror_chotot(raw_records):
    """Optional mirror; failures retain IDs for reconciliation from the raw table."""
    if not os.getenv('MARKET_LISTINGS_TABLE'):
        return 0
    from market_data import normalize_chotot
    from uuid import uuid4
    store = MarketStore()
    now = utc_now()
    run = {'run_id': str(uuid4()), 'source': 'chotot', 'mode': 'mirror', 'started_at': now, 'finished_at': None, 'status': 'running', 'observed': len(raw_records), 'saved': 0, 'rejected': 0, 'reason': None}
    store.save_run(run)
    failed = []
    rows = []
    for raw in raw_records:
        try:
            row = normalize_chotot(raw, now)
            store.upsert(row)
            rows.append(row)
            run['saved'] += 1
        except Exception:
            failed.append(str(raw.get('ad', raw).get('list_id', 'unknown')))
    if failed:
        store.metadata.put_item(Item={'key': f"reconcile#{run['run_id']}", 'source': 'chotot', 'external_ids': failed, 'created_at': now})
    archive_failed = False
    try:
        run['archive_key'] = store.archive('chotot', run['run_id'], rows)
    except Exception:
        archive_failed = True
    run.update(finished_at=utc_now(), status='partial' if failed or archive_failed else ('success' if rows else 'failed'), reason='normalized_write_failed' if failed else ('archive_failed' if archive_failed else None))
    store.save_run(run)
    if failed or archive_failed:
        raise RuntimeError(f"Chợ Tốt raw saved; normalized mirror requires reconciliation: {len(failed)} IDs; archive_failed={archive_failed}; run={run['run_id']}")
    return run['saved']
