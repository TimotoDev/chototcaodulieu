#!/usr/bin/env python3
"""Scoped additive deployment. Default prints plan; mutations require an explicit flag.

Never runs legacy broad IAM/bucket-policy deployment scripts or replaces unrelated env.
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

ROOT=Path(__file__).resolve().parents[1]
REGION='ap-southeast-1'
ACCOUNT='404850807717'
LISTINGS='motorbike-market-listings'
META='motorbike-market-metadata'
ARCHIVE=f'timoto-market-archive-{ACCOUNT}'
FUNCTION='motorbike-market-snapshot'
ENV={'MARKET_LISTINGS_TABLE':LISTINGS,'MARKET_METADATA_TABLE':META,'MARKET_ARCHIVE_BUCKET':ARCHIVE}


def policy(actions,resources):
    return {'Effect':'Allow','Action':actions,'Resource':resources}


def enable_backups(ddb,name,sleep=time.sleep):
    for attempt in range(12):
        try:
            return ddb.update_continuous_backups(TableName=name,PointInTimeRecoverySpecification={'PointInTimeRecoveryEnabled':True})
        except ClientError as error:
            if error.response['Error']['Code']!='ContinuousBackupsUnavailableException' or attempt==11:
                raise
            sleep(5)


def resource_plan():
    listing_arn=f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{LISTINGS}'
    meta_arn=f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{META}'
    return {
        'account':ACCOUNT,'region':REGION,'tables':[LISTINGS,META],
        'private_archive':{'bucket':ARCHIVE,'encryption':'AES256','block_public_access':True,'retention_days':90},
        'existing_role_policy_addition':{'role':'chotot-lambda-role','name':'market-ingestion','document':{'Version':'2012-10-17','Statement':[policy(['dynamodb:GetItem','dynamodb:PutItem','dynamodb:UpdateItem'],[listing_arn,meta_arn]),policy(['s3:PutObject'],f'arn:aws:s3:::{ARCHIVE}/chotot/*')]}},
        'snapshot_role_policy':{'Version':'2012-10-17','Statement':[policy(['dynamodb:Scan'],listing_arn),policy(['dynamodb:GetItem','dynamodb:PutItem'],meta_arn),policy(['logs:CreateLogStream','logs:PutLogEvents'],f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/{FUNCTION}:*')]},
        'new_lambda':FUNCTION,'schedule':'rate(1 hour)',
        'api_changes':'Add shared table env only; retain current role and routes',
        'source_changes':'Daily/refresh code update with opt-in normalized writes; retain schedules and raw storage',
    }


def ensure_resources(session):
    plan=resource_plan();ddb=session.client('dynamodb');s3=session.client('s3');iam=session.client('iam')
    for name,key in [(LISTINGS,'listing_id'),(META,'key')]:
        try:
            table=ddb.describe_table(TableName=name)['Table']
            if table['KeySchema']!=[{'AttributeName':key,'KeyType':'HASH'}]:raise RuntimeError('Unexpected existing table schema')
            if name==LISTINGS and not any(i['IndexName']=='source-order-index' for i in table.get('GlobalSecondaryIndexes',[])):raise RuntimeError('Existing table lacks required index')
        except ddb.exceptions.ResourceNotFoundException:
            params={'TableName':name,'KeySchema':[{'AttributeName':key,'KeyType':'HASH'}],'AttributeDefinitions':[{'AttributeName':key,'AttributeType':'S'}],'BillingMode':'PAY_PER_REQUEST','Tags':[{'Key':'Project','Value':'motorbike-market-data'}]}
            if name==LISTINGS:
                params['AttributeDefinitions'] += [{'AttributeName':'source','AttributeType':'S'},{'AttributeName':'source_order','AttributeType':'S'}]
                params['GlobalSecondaryIndexes']=[{'IndexName':'source-order-index','KeySchema':[{'AttributeName':'source','KeyType':'HASH'},{'AttributeName':'source_order','KeyType':'RANGE'}],'Projection':{'ProjectionType':'ALL'}}]
            ddb.create_table(**params)
            ddb.get_waiter('table_exists').wait(TableName=name,WaiterConfig={'Delay':2,'MaxAttempts':60})
        enable_backups(ddb,name)
    if ddb.describe_time_to_live(TableName=META)['TimeToLiveDescription'].get('TimeToLiveStatus')=='DISABLED':
        ddb.update_time_to_live(TableName=META,TimeToLiveSpecification={'Enabled':True,'AttributeName':'expires_at'})
    try:s3.head_bucket(Bucket=ARCHIVE)
    except ClientError as error:
        if error.response['Error']['Code'] not in ('404','NoSuchBucket'):raise
        s3.create_bucket(Bucket=ARCHIVE,CreateBucketConfiguration={'LocationConstraint':REGION})
    s3.put_public_access_block(Bucket=ARCHIVE,PublicAccessBlockConfiguration={k:True for k in ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets')})
    s3.put_bucket_encryption(Bucket=ARCHIVE,ServerSideEncryptionConfiguration={'Rules':[{'ApplyServerSideEncryptionByDefault':{'SSEAlgorithm':'AES256'}}]})
    s3.put_bucket_lifecycle_configuration(Bucket=ARCHIVE,LifecycleConfiguration={'Rules':[{'ID':'collector-archive-90-days','Status':'Enabled','Filter':{'Prefix':''},'Expiration':{'Days':90},'AbortIncompleteMultipartUpload':{'DaysAfterInitiation':1}}]})
    addition=plan['existing_role_policy_addition']
    iam.put_role_policy(RoleName=addition['role'],PolicyName=addition['name'],PolicyDocument=json.dumps(addition['document']))
    role_name=f'{FUNCTION}-role'
    try:role=iam.get_role(RoleName=role_name)['Role']
    except iam.exceptions.NoSuchEntityException:
        role=iam.create_role(RoleName=role_name,AssumeRolePolicyDocument=json.dumps({'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'lambda.amazonaws.com'},'Action':'sts:AssumeRole'}]}))['Role']
    iam.put_role_policy(RoleName=role_name,PolicyName='market-snapshot',PolicyDocument=json.dumps(plan['snapshot_role_policy']))
    logs=session.client('logs')
    try:logs.create_log_group(logGroupName=f'/aws/lambda/{FUNCTION}')
    except logs.exceptions.ResourceAlreadyExistsException:pass
    logs.put_retention_policy(logGroupName=f'/aws/lambda/{FUNCTION}',retentionInDays=30)
    return role['Arn']


def package_sources(names,existing=None):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
        if existing:
            with zipfile.ZipFile(io.BytesIO(existing)) as current:
                for item in current.infolist():
                    if item.filename not in names:target.writestr(item,current.read(item.filename))
        for name in names:target.write(ROOT/name,arcname=name)
    return out.getvalue()


def update_existing(session,name,names,enable=True):
    client=session.client('lambda');current=client.get_function(FunctionName=name)
    # Immutable AWS version retains the previous code/config for explicit rollback.
    previous=client.publish_version(FunctionName=name,Description='Before unified market-data cutover')['Version']
    with urllib.request.urlopen(current['Code']['Location'],timeout=60) as response:old=response.read()
    code=package_sources(names,old)
    client.update_function_code(FunctionName=name,ZipFile=code)
    client.get_waiter('function_updated_v2').wait(FunctionName=name)
    env=current['Configuration'].get('Environment',{}).get('Variables',{})
    if enable:env.update(ENV)
    client.update_function_configuration(FunctionName=name,Environment={'Variables':env})
    client.get_waiter('function_updated_v2').wait(FunctionName=name)
    print(json.dumps({'function':name,'rollback_version':previous,'code_updated':True,'shared_storage_enabled':enable}),flush=True)


def deploy_snapshot(session,role):
    client=session.client('lambda');code=package_sources(['market_snapshot.py','market_data.py','market_store.py'])
    try:
        client.get_function(FunctionName=FUNCTION)
        client.update_function_code(FunctionName=FUNCTION,ZipFile=code)
        client.get_waiter('function_updated_v2').wait(FunctionName=FUNCTION)
    except client.exceptions.ResourceNotFoundException:
        for attempt in range(12):
            try:
                client.create_function(FunctionName=FUNCTION,Runtime='python3.12',Role=role,Handler='market_snapshot.handler',Code={'ZipFile':code},Timeout=300,MemorySize=512,Environment={'Variables':ENV})
                break
            except client.exceptions.InvalidParameterValueException:
                if attempt==11:raise
                time.sleep(3)
        client.get_waiter('function_active_v2').wait(FunctionName=FUNCTION)
    events=session.client('events')
    arn=events.put_rule(Name=FUNCTION,ScheduleExpression='rate(1 hour)',State='ENABLED')['RuleArn']
    function_arn=f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}'
    target=events.put_targets(Rule=FUNCTION,Targets=[{'Id':'snapshot','Arn':function_arn}])
    if target.get('FailedEntryCount'):raise RuntimeError('Snapshot target failed')
    try:client.add_permission(FunctionName=FUNCTION,StatementId='hourly-snapshot',Action='lambda:InvokeFunction',Principal='events.amazonaws.com',SourceArn=arn)
    except client.exceptions.ResourceConflictException:pass


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',default='harry')
    parser.add_argument('--apply-resources',action='store_true')
    parser.add_argument('--deploy-collectors',action='store_true')
    parser.add_argument('--deploy-api',action='store_true')
    parser.add_argument('--deploy-snapshot',action='store_true')
    args=parser.parse_args()
    session=boto3.Session(profile_name=args.profile,region_name=REGION)
    if session.client('sts').get_caller_identity()['Account']!=ACCOUNT:raise RuntimeError('Wrong AWS account')
    print(json.dumps(resource_plan(),ensure_ascii=False,indent=2))
    role=None
    if args.apply_resources:role=ensure_resources(session)
    if args.deploy_snapshot:
        role=role or session.client('iam').get_role(RoleName=f'{FUNCTION}-role')['Role']['Arn']
        deploy_snapshot(session,role)
    if args.deploy_collectors:
        for name,handler in [('chotot-daily-scrape','lambda_handler.py'),('chotot-weekly-refresh','refresh_handler.py')]:
            update_existing(session,name,['chotot.py',handler,'market_data.py','market_store.py'])
    if args.deploy_api:update_existing(session,'chotot-dashboard-api',['api_handler.py','market_data.py','market_read.py','market_store.py'])


if __name__=='__main__':main()
