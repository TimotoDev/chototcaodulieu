"""Staging-only, evidence intake. No model invocation or production writes."""
import base64
import gzip
import hashlib
import json
import os
import re
import time

import boto3
from botocore.exceptions import ClientError


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def safe(value, maximum=256):
    value = str(value or '')
    if len(value) > maximum or not re.fullmatch(r'[A-Za-z0-9_./:<>{}\-]*', value):
        return '[omitted]'
    return value


def normalize(raw, envelope, log_event):
    if raw.get('schema_version') != 1 or raw.get('service') != 'timoto-backend' or raw.get('environment') != 'staging':
        return None
    kind = raw.get('event')
    if kind != 'api_exception' and not (kind == 'api_request' and isinstance(raw.get('status'), int) and 500 <= raw['status'] < 600):
        return None
    # Never forward arbitrary exception text, SQL, HTTP bodies, or headers.
    fields = ('event', 'service', 'environment', 'release_sha', 'request_id', 'error_id', 'error_code', 'exception_type', 'method', 'route')
    clean = {key: safe(raw.get(key)) for key in fields}
    clean['schema_version'] = 1
    clean['aws_region'] = 'ap-southeast-7'
    clean['status'] = raw.get('status') if isinstance(raw.get('status'), int) else None
    clean['message'] = '[omitted: arbitrary exception text]'
    clean['stack_frames'] = [
        {'file': safe(f.get('file')), 'function': safe(f.get('function')), 'line': f.get('line') if isinstance(f.get('line'), int) else None}
        for f in raw.get('stack_frames', [])[-10:] if isinstance(f, dict)
    ]
    clean['source_event_id'] = log_event['id']
    clean['source_timestamp_ms'] = log_event['timestamp']
    clean['log_group'] = envelope['logGroup']
    clean['log_stream'] = envelope['logStream']
    clean['fingerprint_hash'] = digest(str(raw.get('error_fingerprint') or raw.get('error_code') or kind) + ':' + clean['route'])
    return clean


def forward(event, context):
    payload = json.loads(gzip.decompress(base64.b64decode(event['awslogs']['data'])))
    if payload.get('messageType') == 'CONTROL_MESSAGE':
        return {'control': True}
    if payload.get('owner') != os.environ['SOURCE_ACCOUNT'] or payload.get('logGroup') != '/ecs/timoto-backend-fargate':
        raise ValueError('unexpected source')
    queue = boto3.client('sqs', region_name='ap-southeast-1')
    count = 0
    for item in payload['logEvents']:
        try:
            raw = json.loads(item['message'])
        except (ValueError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        clean = normalize(raw, payload, item)
        if clean:
            queue.send_message(QueueUrl=os.environ['QUEUE_URL'], MessageBody=json.dumps(clean))
            count += 1
    return {'forwarded': count}


def collect_one(body, ddb, s3):
    table = os.environ['TABLE_NAME']
    event_key = 'event#' + digest(body['log_group'] + ':' + body['log_stream'] + ':' + body['source_event_id'])
    if ddb.get_item(TableName=table, Key={'pk': {'S': event_key}}, ConsistentRead=True).get('Item'):
        return 'duplicate'
    # Only exception records have a reliable error class/frame fingerprint.
    # Request summaries are stored as correlation evidence, not separate incidents.
    window = int(body['source_timestamp_ms']) // 900000
    incident_id = digest(':'.join([body['service'], body['environment'], body['release_sha'], body['fingerprint_hash'], str(window)]))
    key = 'agent-evidence/staging/events/' + event_key[6:] + '.json'
    evidence = {
        'schema_version': 1, 'kind': 'observability_evidence',
        'incident_id': incident_id if body['event'] == 'api_exception' else None,
        'repository': 'TimotoDev/timoto-mobile', 'event': body,
        'release_binding': 'unverified: runtime SHA may describe a dirty build',
        'analysis_status': 'not_analyzed',
    }
    s3.put_object(Bucket=os.environ['BUCKET'], Key=key, Body=json.dumps(evidence).encode(), ServerSideEncryption='AES256')
    expires = int(time.time()) + 30 * 86400
    operations = [{'Put': {'TableName': table, 'Item': {
        'pk': {'S': event_key}, 'expires_at': {'N': str(expires)}, 's3_key': {'S': key},
    }, 'ConditionExpression': 'attribute_not_exists(pk)'}}]
    if body['event'] == 'api_exception':
        operations.append({'Update': {
            'TableName': table, 'Key': {'pk': {'S': 'incident#' + incident_id}},
            'UpdateExpression': 'SET evidence_key = :key, expires_at = :ttl, review_partition = :partition, review_due = :now, #state = if_not_exists(#state, :state) ADD event_count :one',
            'ExpressionAttributeNames': {'#state': 'state'},
            'ExpressionAttributeValues': {':key': {'S': key}, ':ttl': {'N': str(expires)}, ':partition': {'S': 'staging'}, ':now': {'N': str(int(time.time()))}, ':state': {'S': 'EVIDENCE_COLLECTED'}, ':one': {'N': '1'}},
        }})
    try:
        ddb.transact_write_items(TransactItems=operations)
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'TransactionCanceledException' and ddb.get_item(TableName=table, Key={'pk': {'S': event_key}}, ConsistentRead=True).get('Item'):
            return 'duplicate'
        raise
    return 'collected'


def collect(event, context):
    ddb, s3 = boto3.client('dynamodb'), boto3.client('s3')
    failures = []
    for record in event['Records']:
        try:
            result = collect_one(json.loads(record['body']), ddb, s3)
            print(json.dumps({'message_id': record['messageId'], 'result': result}))
        except Exception:
            # Avoid logging raw payloads or error strings that may include data.
            failures.append({'itemIdentifier': record['messageId']})
    return {'batchItemFailures': failures}
