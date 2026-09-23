"""Run CLI version check only; no task role, secrets or model requests."""
import json
import boto3

s = boto3.Session(profile_name='harry', region_name='ap-southeast-1')
ecs = s.client('ecs')
service = ecs.describe_services(cluster='timoto-ai-dev', services=['timoto-ai-dev-incident-orchestrator'])['services'][0]
base = ecs.describe_task_definition(taskDefinition=service['taskDefinition'])['taskDefinition']
image = s.client('ecr').describe_images(repositoryName='timoto-ai/repair-agent',
    imageIds=[{'imageTag': 'antigravity-cli-1.2.5'}])['imageDetails'][0]['imageDigest']
td = ecs.register_task_definition(family='timoto-ai-antigravity-cli-smoke',
    requiresCompatibilities=['FARGATE'], networkMode='awsvpc', cpu='256', memory='512',
    executionRoleArn=base['executionRoleArn'], runtimePlatform=base['runtimePlatform'],
    containerDefinitions=[{'name': 'cli-smoke', 'essential': True,
        'image': '404850807717.dkr.ecr.ap-southeast-1.amazonaws.com/timoto-ai/repair-agent@' + image,
        'command': ['--version'], 'readonlyRootFilesystem': True,
        'logConfiguration': {'logDriver': 'awslogs', 'options': {
            'awslogs-group': '/ecs/timoto-ai/dev/repair-agent',
            'awslogs-region': 'ap-southeast-1', 'awslogs-stream-prefix': 'smoke'}}}])['taskDefinition']
r = ecs.run_task(cluster='timoto-ai-dev', taskDefinition=td['taskDefinitionArn'],
    launchType='FARGATE', networkConfiguration=service['networkConfiguration'])
if r.get('failures'):
    raise RuntimeError(r['failures'])
print(json.dumps({'task': r['tasks'][0]['taskArn'], 'image_digest': image,
                  'task_definition': td['taskDefinitionArn']}))
