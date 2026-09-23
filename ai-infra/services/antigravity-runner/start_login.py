"""One-off OAuth bootstrap. No model API keys, no production permissions."""
import json
import time
import boto3

s = boto3.Session(profile_name='harry', region_name='ap-southeast-1')
iam, ecs = s.client('iam'), s.client('ecs')
role_name = 'timoto-ai-antigravity-login'
try:
    role = iam.get_role(RoleName=role_name)['Role']
except iam.exceptions.NoSuchEntityException:
    role = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps({
        'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow',
        'Principal': {'Service': 'ecs-tasks.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}))['Role']
iam.put_role_policy(RoleName=role_name, PolicyName='ecs-exec-channels', PolicyDocument=json.dumps({
    'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Resource': '*',
    'Action': ['ssmmessages:CreateControlChannel', 'ssmmessages:CreateDataChannel',
               'ssmmessages:OpenControlChannel', 'ssmmessages:OpenDataChannel']}]}))
service = ecs.describe_services(cluster='timoto-ai-dev', services=['timoto-ai-dev-incident-orchestrator'])['services'][0]
base = ecs.describe_task_definition(taskDefinition='timoto-ai-antigravity-cli-smoke:1')['taskDefinition']
td = ecs.register_task_definition(family='timoto-ai-antigravity-login',
    requiresCompatibilities=['FARGATE'], networkMode='awsvpc', cpu='256', memory='512',
    executionRoleArn=base['executionRoleArn'], taskRoleArn=role['Arn'],
    runtimePlatform=base['runtimePlatform'], containerDefinitions=[{
        'name': 'login', 'image': base['containerDefinitions'][0]['image'],
        'essential': True, 'entryPoint': ['sleep'], 'command': ['1800'],
        'linuxParameters': {'initProcessEnabled': True}}])['taskDefinition']
time.sleep(10)
result = ecs.run_task(cluster='timoto-ai-dev', taskDefinition=td['taskDefinitionArn'],
    launchType='FARGATE', enableExecuteCommand=True,
    networkConfiguration=service['networkConfiguration'], startedBy='antigravity-oauth-bootstrap')
if result.get('failures'):
    raise RuntimeError(result['failures'])
print(json.dumps({'task': result['tasks'][0]['taskArn'], 'expires_after_seconds': 1800}))
