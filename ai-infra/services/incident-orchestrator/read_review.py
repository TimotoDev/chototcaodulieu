"""Read a staging review using operator credentials; performs no writes."""
import argparse
import json
import re
import boto3

parser = argparse.ArgumentParser()
parser.add_argument('incident_id')
args = parser.parse_args()
if not re.fullmatch('[0-9a-f]{64}', args.incident_id):
    parser.error('expected a 64-character incident hash')
session = boto3.Session(profile_name='harry', region_name='ap-southeast-1')
item = session.resource('dynamodb').Table('timoto-ai-staging-evidence').get_item(
    Key={'pk': 'incident#' + args.incident_id}, ConsistentRead=True).get('Item', {})
if not item.get('report_key'):
    print(json.dumps({'review_status': item.get('review_status', 'NOT_REVIEWED')}))
else:
    report = session.client('s3').get_object(Bucket='timoto-ai-dev-artifacts-1d316272', Key=item['report_key'])
    print(json.dumps(json.loads(report['Body'].read()), indent=2, ensure_ascii=False))
