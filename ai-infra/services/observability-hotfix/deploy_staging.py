"""Deploy only the tested overlay, with concurrent deployment protection."""
import json
import boto3

session = boto3.Session(profile_name='harry', region_name='ap-southeast-7')
ecs, ecr = session.client('ecs'), session.client('ecr')
service = ecs.describe_services(cluster='timoto-backend-fargate', services=['timoto-backend'])['services'][0]
assert service['taskDefinition'].endswith(':536'), 'Staging changed; re-review before deployment'
base = ecs.describe_task_definition(taskDefinition=service['taskDefinition'])['taskDefinition']
digest = ecr.describe_images(repositoryName='timoto-backend', imageIds=[{'imageTag':'obsfix-20260916-correlation-v1'}])['imageDetails'][0]['imageDigest']
allowed = set(ecs.meta.service_model.operation_model('RegisterTaskDefinition').input_shape.members)
new = {k:v for k,v in base.items() if k in allowed}
container = next(c for c in new['containerDefinitions'] if c['name'] == 'django-app')
container['image'] = '404850807717.dkr.ecr.ap-southeast-7.amazonaws.com/timoto-backend@' + digest
environment = {e['name']:e['value'] for e in container.get('environment', [])}
environment['RUN_MIGRATIONS'] = 'false'
environment['OBSERVABILITY_PATCH'] = '20260916-correlation-v1'
container['environment'] = [{'name':k,'value':v} for k,v in environment.items()]
registered = ecs.register_task_definition(**new)['taskDefinition']['taskDefinitionArn']
latest = ecs.describe_services(cluster='timoto-backend-fargate', services=['timoto-backend'])['services'][0]
assert latest['taskDefinition'] == service['taskDefinition'], 'Concurrent deployment detected'
ecs.update_service(cluster='timoto-backend-fargate', service='timoto-backend', taskDefinition=registered)
print(json.dumps({'previous_task_definition':service['taskDefinition'],'task_definition':registered,'image_digest':digest,'migrations':False}))
