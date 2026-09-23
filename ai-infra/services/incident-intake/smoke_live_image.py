"""Run an isolated Django request against the deployed image, without DB/secrets.

No public endpoint is added; this is not an external HTTP test of the ECS service.
"""
import json
import uuid
import boto3

session = boto3.Session(profile_name='harry', region_name='ap-southeast-7')
ecs = session.client('ecs')
service = ecs.describe_services(cluster='timoto-backend-fargate', services=['timoto-backend'])['services'][0]
tasks = ecs.list_tasks(cluster='timoto-backend-fargate', serviceName='timoto-backend')['taskArns']
live = next(t for t in ecs.describe_tasks(cluster='timoto-backend-fargate', tasks=tasks)['tasks']
            if t['taskDefinitionArn'] == service['taskDefinition'] and t['lastStatus'] == 'RUNNING')
definition = ecs.describe_task_definition(taskDefinition=live['taskDefinitionArn'])['taskDefinition']
container = next(c for c in definition['containerDefinitions'] if c['name'] == 'django-app')
running = next(c for c in live['containers'] if c['name'] == 'django-app')
image = container['image'].split('@')[0].split(':')[0] + '@' + running['imageDigest']
release = next((e['value'] for e in container.get('environment', []) if e['name'] == 'RELEASE_SHA'), 'unknown')
marker = 'agent-smoke-' + uuid.uuid4().hex[:12]
code = '''
import json, logging, sys, types
from django.conf import settings
settings.configure(SECRET_KEY='isolated-smoke', DEBUG=False, SERVICE_NAME='timoto-backend', ENVIRONMENT='staging', AWS_REGION='ap-southeast-7', RELEASE_SHA=RELEASE_VALUE, ROOT_URLCONF='smoke_urls', ALLOWED_HOSTS=['testserver'], INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes'], DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}}, REST_FRAMEWORK={'UNAUTHENTICATED_USER':None,'EXCEPTION_HANDLER':'apps.bikes.exceptions.custom_exception_handler'}, MIDDLEWARE=['apps.bikes.middleware.APIRequestLoggingMiddleware','apps.bikes.middleware.APIExceptionMiddleware'])
import django
django.setup()
from rest_framework.views import APIView
from django.urls import path
from django.test import Client
class Smoke(APIView):
    authentication_classes=[]
    permission_classes=[]
    def get(self, request):
        raise RuntimeError('controlled-agent-smoke-no-user-data')
urls=types.ModuleType('smoke_urls')
urls.urlpatterns=[path('api/agent-smoke/',Smoke.as_view())]
sys.modules['smoke_urls']=urls
events=[]
class Capture(logging.Handler):
    def emit(self, record):
        events.append(json.loads(record.getMessage()))
        print(record.getMessage(), flush=True)
for name in ['api.request','api.exception']:
    logger=logging.getLogger(name)
    logger.handlers=[Capture()]
    logger.setLevel(logging.INFO)
    logger.propagate=False
response=Client(raise_request_exception=False).get('/api/agent-smoke/',HTTP_X_REQUEST_ID=MARKER_VALUE)
request=next(e for e in events if e['event']=='api_request')
exception=next(e for e in events if e['event']=='api_exception')
print(json.dumps({'smoke_marker':MARKER_VALUE,'status':response.status_code,'correlated':request['error_id']==exception['error_id'],'stack_present':bool(exception['stack_frames']),'response_request_id_matches':response.json()['request_id']==request['request_id']}),flush=True)
'''.replace('RELEASE_VALUE', repr(release)).replace('MARKER_VALUE', repr(marker))
registered = ecs.register_task_definition(
    family='timoto-ai-staging-observability-smoke', networkMode='awsvpc', requiresCompatibilities=['FARGATE'],
    cpu=definition['cpu'], memory=definition['memory'], executionRoleArn=definition['executionRoleArn'],
    runtimePlatform=definition.get('runtimePlatform', {'cpuArchitecture':'X86_64','operatingSystemFamily':'LINUX'}),
    containerDefinitions=[{'name':'smoke','image':image,'essential':True,'entryPoint':['python','-c'], 'command':[code],
                          'logConfiguration':container['logConfiguration']}],
)['taskDefinition']['taskDefinitionArn']
result = ecs.run_task(cluster='timoto-backend-fargate', taskDefinition=registered, launchType='FARGATE',
                      networkConfiguration=service['networkConfiguration'], startedBy=marker)
if result.get('failures'):
    raise RuntimeError(result['failures'])
print(json.dumps({'marker':marker,'task':result['tasks'][0]['taskArn'],'task_definition':registered,'image':image,'release':release}))
