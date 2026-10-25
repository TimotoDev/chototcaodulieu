#!/usr/bin/env python3
"""Deploy weekly full listing refresh Lambda + EventBridge schedule."""

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
FN_NAME = "chotot-weekly-refresh"
RULE_NAME = "chotot-weekly-refresh"
RULE_SCHEDULE = "cron(0 18 ? * SUN *)"  # Sunday 01:00 VN
BUCKET = "chotot-dashboard-404850807717"
DDB_TABLE = "chotot-xe-may"
ROOT = Path(__file__).resolve().parent


def build_zip() -> bytes:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        for name in ("chotot.py", "refresh_handler.py", "market_data.py", "market_store.py"):
            shutil.copy2(ROOT / name, tdir / name)
        subprocess.check_call(
            ["python3", "-m", "pip", "install", "-q", "-t", str(tdir), "requests", "boto3"]
        )
        buff = io.BytesIO()
        with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in tdir.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(tdir)))
        return buff.getvalue()


def ensure_policy(iam, account_id: str) -> None:
    doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:Scan",
                    "dynamodb:BatchWriteItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:GetItem",
                ],
                "Resource": f"arn:aws:dynamodb:{REGION}:{account_id}:table/{DDB_TABLE}",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": f"arn:aws:s3:::{BUCKET}/data/*",
            },
            {
                "Effect": "Allow",
                "Action": ["lambda:InvokeFunction"],
                "Resource": f"arn:aws:lambda:{REGION}:{account_id}:function:{FN_NAME}",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="chotot-weekly-refresh-inline",
        PolicyDocument=json.dumps(doc),
    )
    print("[ok] IAM policy updated")


def deploy_lambda(lmb, role_arn: str, zip_bytes: bytes) -> str:
    env = {
        "Variables": {
            "CHOTOT_DDB_TABLE": DDB_TABLE,
            "CHOTOT_EXPORT_S3_BUCKET": BUCKET,
            "REFRESH_LAMBDA_NAME": FN_NAME,
        }
    }
    try:
        lmb.get_function(FunctionName=FN_NAME)
        lmb.update_function_code(FunctionName=FN_NAME, ZipFile=zip_bytes)
        time.sleep(5)
        lmb.update_function_configuration(
            FunctionName=FN_NAME,
            Role=role_arn,
            Handler="refresh_handler.handler",
            Runtime="python3.11",
            Timeout=900,
            MemorySize=512,
            Environment=env,
        )
        print(f"[ok] lambda updated: {FN_NAME}")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        arn = lmb.create_function(
            FunctionName=FN_NAME,
            Runtime="python3.11",
            Role=role_arn,
            Handler="refresh_handler.handler",
            Code={"ZipFile": zip_bytes},
            Timeout=900,
            MemorySize=512,
            Environment=env,
        )["FunctionArn"]
        print(f"[ok] lambda created: {arn}")
    return lmb.get_function(FunctionName=FN_NAME)["Configuration"]["FunctionArn"]


def deploy_schedule(events, lam_arn: str, account_id: str) -> None:
    rule_arn = events.put_rule(
        Name=RULE_NAME,
        ScheduleExpression=RULE_SCHEDULE,
        State="ENABLED",
        Description="Weekly full Chotot listing refresh — Sunday 01:00 VN",
    )["RuleArn"]
    lam = boto3.Session(profile_name=PROFILE, region_name=REGION).client("lambda")
    try:
        lam.add_permission(
            FunctionName=FN_NAME,
            StatementId="allow-eventbridge-weekly-refresh",
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=rule_arn,
        )
    except ClientError:
        pass

    events.put_targets(
        Rule=RULE_NAME,
        Targets=[{
            "Id": "1",
            "Arn": lam_arn,
            "Input": json.dumps({"reset": True, "dry_run": False}),
        }],
    )
    print(f"[ok] schedule: {RULE_NAME} ({RULE_SCHEDULE})")


def main():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    sts = session.client("sts")
    iam = session.client("iam")
    lmb = session.client("lambda")
    events = session.client("events")

    account_id = sts.get_caller_identity()["Account"]
    role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]

    print("Building Lambda package...")
    zip_bytes = build_zip()
    print(f"ZIP size: {len(zip_bytes) / 1024 / 1024:.1f} MB")

    ensure_policy(iam, account_id)
    fn_arn = deploy_lambda(lmb, role_arn, zip_bytes)
    deploy_schedule(events, fn_arn, account_id)

    print("\nDone.")
    print(f"  Lambda:   {FN_NAME}")
    print(f"  Schedule: Sunday 01:00 VN (cron 18:00 UTC Saturday)")
    print("  Manual:   invoke with {\"reset\": true}")


if __name__ == "__main__":
    main()
