#!/usr/bin/env python3
"""Deploy Lambda-only image sync stack: Producer + SQS + Worker + DLQ + EventBridge."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


PROFILE = os.getenv("AWS_PROFILE", "harry")
REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")

ROLE_NAME = "chotot-lambda-role"
SOURCE_TABLE = "chotot-xe-may"
STATE_TABLE = "chotot-image-sync-state"
SOURCE_INDEX = "date-index"

BUCKET = "chotot-dashboard-404850807717"
S3_PREFIX = "images"

DLQ_NAME = "chotot-image-sync-dlq"
QUEUE_NAME = "chotot-image-sync-queue"

PRODUCER_FN = "chotot-image-sync-producer"
WORKER_FN = "chotot-image-sync-worker"
RULE_NAME = "chotot-image-sync-daily"
RULE_SCHEDULE = "cron(20 17 * * ? *)"  # 00:20 VN

ROOT = Path(__file__).resolve().parent


def mk_session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def ensure_state_table(ddb):
    try:
        ddb.describe_table(TableName=STATE_TABLE)
        print(f"[ok] table exists: {STATE_TABLE}")
        return
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    ddb.create_table(
        TableName=STATE_TABLE,
        AttributeDefinitions=[{"AttributeName": "s3_key", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "s3_key", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=STATE_TABLE)
    print(f"[ok] table created: {STATE_TABLE}")


def ensure_queue(sqs, name, redrive_policy=None):
    attrs = {
        "VisibilityTimeout": "120",
        "MessageRetentionPeriod": "1209600",
    }
    if redrive_policy:
        attrs["RedrivePolicy"] = json.dumps(redrive_policy)

    try:
        url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
        sqs.set_queue_attributes(QueueUrl=url, Attributes=attrs)
        print(f"[ok] queue exists: {name}")
    except ClientError:
        resp = sqs.create_queue(QueueName=name, Attributes=attrs)
        url = resp["QueueUrl"]
        print(f"[ok] queue created: {name}")

    qattr = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])
    arn = qattr["Attributes"]["QueueArn"]
    return url, arn


def ensure_role(iam):
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
        role_arn = role["Arn"]
        print(f"[ok] role exists: {ROLE_NAME}")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        role = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust))["Role"]
        role_arn = role["Arn"]
        print(f"[ok] role created: {ROLE_NAME}")

    # Basic Lambda logging policy
    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

    return role_arn


def put_inline_policy(iam, account_id, queue_arn, dlq_arn):
    doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["dynamodb:Query", "dynamodb:Scan", "dynamodb:DescribeTable"],
                "Resource": [
                    f"arn:aws:dynamodb:{REGION}:{account_id}:table/{SOURCE_TABLE}",
                    f"arn:aws:dynamodb:{REGION}:{account_id}:table/{SOURCE_TABLE}/index/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
                "Resource": [f"arn:aws:dynamodb:{REGION}:{account_id}:table/{STATE_TABLE}"],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "sqs:SendMessage",
                    "sqs:SendMessageBatch",
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:ChangeMessageVisibility",
                    "sqs:GetQueueAttributes",
                ],
                "Resource": [queue_arn, dlq_arn],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{BUCKET}/{S3_PREFIX}/*"],
            },
            {
                "Effect": "Allow",
                "Action": ["lambda:InvokeFunction"],
                "Resource": [f"arn:aws:lambda:{REGION}:{account_id}:function:{PRODUCER_FN}"],
            },
        ],
    }

    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="chotot-image-sync-inline",
        PolicyDocument=json.dumps(doc),
    )
    print("[ok] inline policy updated")


def build_zip(src_file: Path, pip_packages=None) -> bytes:
    if pip_packages is None:
        pip_packages = []

    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        shutil.copy2(src_file, tdir / src_file.name)

        if pip_packages:
            cmd = ["python3", "-m", "pip", "install", "-q", "-t", str(tdir), *pip_packages]
            subprocess.check_call(cmd)

        buff = io.BytesIO()
        with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in tdir.rglob("*"):
                if p.is_file():
                    arc = p.relative_to(tdir)
                    zf.write(p, arcname=str(arc))
        return buff.getvalue()


def wait_lambda_ready(lmb, name: str, timeout_sec: int = 180) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        cfg = lmb.get_function_configuration(FunctionName=name)
        status = cfg.get("LastUpdateStatus", "Successful")
        state = cfg.get("State", "Active")
        if state == "Active" and status in {"Successful", None}:
            return
        time.sleep(2)
    raise TimeoutError(f"Lambda not ready in time: {name}")


def _retry_conflict(fn, retries: int = 20, sleep_sec: int = 2):
    for i in range(retries):
        try:
            return fn()
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code != "ResourceConflictException" or i == retries - 1:
                raise
            time.sleep(sleep_sec)


def upsert_lambda(lmb, name, role_arn, handler, zip_bytes, timeout, memory, env, reserved_concurrency=None):
    exists = True
    try:
        lmb.get_function(FunctionName=name)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            exists = False
        else:
            raise

    if not exists:
        _retry_conflict(lambda: lmb.create_function(
            FunctionName=name,
            Runtime="python3.11",
            Role=role_arn,
            Handler=handler,
            Code={"ZipFile": zip_bytes},
            Timeout=timeout,
            MemorySize=memory,
            Environment={"Variables": env},
            Publish=True,
        ))
        print(f"[ok] lambda created: {name}")
    else:
        wait_lambda_ready(lmb, name)
        _retry_conflict(lambda: lmb.update_function_code(FunctionName=name, ZipFile=zip_bytes, Publish=True))
        wait_lambda_ready(lmb, name)
        _retry_conflict(lambda: lmb.update_function_configuration(
            FunctionName=name,
            Timeout=timeout,
            MemorySize=memory,
            Environment={"Variables": env},
            Handler=handler,
            Runtime="python3.11",
            Role=role_arn,
        ))
        print(f"[ok] lambda updated: {name}")

    wait_lambda_ready(lmb, name)
    if reserved_concurrency is not None:
        _retry_conflict(lambda: lmb.put_function_concurrency(
            FunctionName=name,
            ReservedConcurrentExecutions=reserved_concurrency,
        ))


def ensure_event_source_mapping(lmb, queue_arn, function_name):
    maps = lmb.list_event_source_mappings(EventSourceArn=queue_arn, FunctionName=function_name).get(
        "EventSourceMappings", []
    )
    if maps:
        uuid = maps[0]["UUID"]
        lmb.update_event_source_mapping(
            UUID=uuid,
            Enabled=True,
            BatchSize=10,
            MaximumBatchingWindowInSeconds=2,
            FunctionResponseTypes=["ReportBatchItemFailures"],
        )
        print("[ok] event source mapping updated")
        return

    lmb.create_event_source_mapping(
        EventSourceArn=queue_arn,
        FunctionName=function_name,
        Enabled=True,
        BatchSize=10,
        MaximumBatchingWindowInSeconds=2,
        FunctionResponseTypes=["ReportBatchItemFailures"],
    )
    print("[ok] event source mapping created")


def ensure_rule(events, lmb, producer_arn):
    rule_arn = events.put_rule(Name=RULE_NAME, ScheduleExpression=RULE_SCHEDULE, State="ENABLED")["RuleArn"]
    events.put_targets(
        Rule=RULE_NAME,
        Targets=[
            {
                "Id": "producer",
                "Arn": producer_arn,
                "Input": json.dumps({"mode": "daily"}),
            }
        ],
    )

    stmt_id = "allow-eventbridge-image-sync"
    try:
        lmb.add_permission(
            FunctionName=PRODUCER_FN,
            StatementId=stmt_id,
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=rule_arn,
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceConflictException":
            raise

    print(f"[ok] eventbridge rule configured: {RULE_NAME} ({RULE_SCHEDULE})")


def wait_ready():
    # IAM and Lambda config propagation
    time.sleep(8)


def invoke_smoke_test(lmb):
    payload = {"mode": "daily", "stop_after": 30, "dry_run": False}
    resp = lmb.invoke(
        FunctionName=PRODUCER_FN,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp["Payload"].read().decode("utf-8")
    print("[smoke] producer invoke response:", body)


def main():
    session = mk_session()
    sts = session.client("sts")
    account_id = sts.get_caller_identity()["Account"]

    iam = session.client("iam")
    ddb = session.client("dynamodb")
    sqs = session.client("sqs")
    lmb = session.client("lambda")
    events = session.client("events")

    ensure_state_table(ddb)

    dlq_url, dlq_arn = ensure_queue(sqs, DLQ_NAME)
    queue_url, queue_arn = ensure_queue(
        sqs,
        QUEUE_NAME,
        redrive_policy={"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "5"},
    )

    role_arn = ensure_role(iam)
    put_inline_policy(iam, account_id, queue_arn, dlq_arn)

    producer_zip = build_zip(ROOT / "image_sync_producer.py")
    worker_zip = build_zip(ROOT / "image_sync_worker.py", pip_packages=["requests"])

    producer_env = {
        "SOURCE_TABLE": SOURCE_TABLE,
        "SOURCE_INDEX": SOURCE_INDEX,
        "QUEUE_URL": queue_url,
        "S3_PREFIX": S3_PREFIX,
        "PAGE_LIMIT": "200",
        "STOP_BEFORE_MS": "45000",
    }
    worker_env = {
        "IMAGE_BUCKET": BUCKET,
        "STATE_TABLE": STATE_TABLE,
        "MAX_BYTES": str(25 * 1024 * 1024),
    }

    upsert_lambda(
        lmb,
        PRODUCER_FN,
        role_arn,
        "image_sync_producer.handler",
        producer_zip,
        timeout=900,
        memory=512,
        env=producer_env,
        reserved_concurrency=5,
    )
    upsert_lambda(
        lmb,
        WORKER_FN,
        role_arn,
        "image_sync_worker.handler",
        worker_zip,
        timeout=120,
        memory=1024,
        env=worker_env,
        reserved_concurrency=40,
    )

    wait_ready()

    producer_arn = lmb.get_function(FunctionName=PRODUCER_FN)["Configuration"]["FunctionArn"]
    ensure_event_source_mapping(lmb, queue_arn, WORKER_FN)
    ensure_rule(events, lmb, producer_arn)

    invoke_smoke_test(lmb)

    print("=" * 68)
    print("Deployed image sync Lambda stack")
    print(f"Producer : {PRODUCER_FN}")
    print(f"Worker   : {WORKER_FN}")
    print(f"Queue    : {QUEUE_NAME}")
    print(f"DLQ      : {DLQ_NAME}")
    print(f"StateTbl : {STATE_TABLE}")
    print(f"Schedule : {RULE_SCHEDULE} (00:20 VN)")


if __name__ == "__main__":
    main()
