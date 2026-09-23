"""Inspect only this smoke run, replay the identical source event, verify dedup."""
import argparse
import base64
import gzip
import json
import boto3

parser = argparse.ArgumentParser()
parser.add_argument('task_id')
parser.add_argument('--replay', action='store_true')
args = parser.parse_args()
session = boto3.Session(profile_name='harry')
logs = session.client('logs', region_name='ap-southeast-7')
stream = 'ecs/smoke/' + args.task_id
events = logs.get_log_events(logGroupName='/ecs/timoto-backend-fargate', logStreamName=stream, startFromHead=True)['events']
summary = []
for event in events:
    try:
        body = json.loads(event['message'])
    except ValueError:
        continue
    if 'smoke_marker' in body:
        summary.append(body)
print(json.dumps({'smoke_results': summary}))
s3 = session.client('s3', region_name='ap-southeast-1')
ddb = session.client('dynamodb', region_name='ap-southeast-1')
bucket = 'timoto-ai-dev-artifacts-1d316272'
found = []
for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix='agent-evidence/staging/events/'):
    for obj in page.get('Contents', []):
        evidence = json.loads(s3.get_object(Bucket=bucket, Key=obj['Key'])['Body'].read())
        if evidence['event']['log_stream'] == stream:
            found.append(evidence)
            if evidence['incident_id']:
                row = ddb.get_item(TableName='timoto-ai-staging-evidence', Key={'pk': {'S':'incident#'+evidence['incident_id']}}, ConsistentRead=True)['Item']
                print(json.dumps({'incident_id': evidence['incident_id'], 'count': row['event_count']['N'], 'state': row['state']['S'], 'evidence_key': obj['Key'], 'message_omitted': evidence['event']['message'].startswith('[omitted')}))
print(json.dumps({'evidence_events': len(found)}))
if args.replay:
    assert found, 'No collected evidence yet'
    # CloudWatch FilterLogEvents supplies original event IDs needed for exact replay.
    originals = logs.filter_log_events(logGroupName='/ecs/timoto-backend-fargate', logStreamNames=[stream])['events']
    payload = {'owner':'404850807717', 'messageType':'DATA_MESSAGE', 'logGroup':'/ecs/timoto-backend-fargate', 'logStream':stream,
               'logEvents':[{'id':e['eventId'], 'timestamp':e['timestamp'], 'message':e['message']} for e in originals]}
    wrapped = {'awslogs':{'data':base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()}}
    client = session.client('lambda', region_name='ap-southeast-7')
    for _ in range(2):
        result = client.invoke(FunctionName='timoto-ai-staging-forward', Payload=json.dumps(wrapped).encode())
        assert not result.get('FunctionError'), result['Payload'].read().decode()
        print(result['Payload'].read().decode())
