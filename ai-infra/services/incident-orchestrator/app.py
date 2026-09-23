"""Durable CloudWatch incident consumer for the Timoto AI platform.

This first version is deliberately evidence-first: it consumes an incident,
redacts obvious secret fields, archives the raw event in encrypted S3, and only
then acknowledges the SQS message. It does not mutate production or create a
GitHub change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
from datetime import UTC, datetime
from typing import Any

import boto3
from reviewer import Reviewer


LOG = logging.getLogger("incident-orchestrator")
STOP = False
SENSITIVE_KEYS = {"authorization", "password", "secret", "token", "api_key", "private_key"}


def stop_handler(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(child_key): redact(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [redact(child) for child in value]
    if isinstance(value, str) and len(value) > 20000:
        return value[:20000] + "...[TRUNCATED]"
    return value


def parse_body(raw_body: str) -> Any:
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return {"raw": raw_body[:20000], "parseError": "invalid-json"}


def stable_event_id(message: dict[str, Any], body: Any) -> str:
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict) and detail.get("eventID"):
            return str(detail["eventID"])
        if body.get("id"):
            return str(body["id"])
    message_id = message.get("MessageId")
    if message_id:
        return str(message_id)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return digest[:32]


def archive_message(s3: Any, bucket: str, message: dict[str, Any]) -> str:
    body = parse_body(str(message.get("Body", "")))
    event_id = stable_event_id(message, body)
    received_at = datetime.now(UTC)
    envelope = {
        "schemaVersion": "1",
        "eventId": event_id,
        "receivedAt": received_at.isoformat(),
        "source": "aws.cloudwatch-orchestrator",
        "sqsMessageId": message.get("MessageId"),
        "event": redact(body),
    }
    key = f"incidents/{received_at:%Y/%m/%d}/{event_id}.json"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    return key


def main() -> None:
    queue_url = os.environ["INCIDENT_QUEUE_URL"]
    bucket = os.environ["ARTIFACTS_BUCKET"]
    region = os.getenv("AWS_REGION", "ap-southeast-1")
    sqs = boto3.client("sqs", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    LOG.info("incident consumer started")
    reviewer = Reviewer() if os.getenv('REVIEW_TABLE') else None

    while not STOP:
        if reviewer:
            try:
                reviewer.tick()
            except Exception as exc:
                LOG.warning('review_poll_failed error_type=%s', type(exc).__name__)
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
            VisibilityTimeout=900,
        )
        for message in response.get("Messages", []):
            message_id = message.get("MessageId", "unknown")
            try:
                key = archive_message(s3, bucket, message)
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
                LOG.info("incident archived message_id=%s s3_key=%s", message_id, key)
            except Exception:
                LOG.exception("incident processing failed message_id=%s; leaving message for retry", message_id)

    LOG.info("incident consumer stopped")


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    main()
