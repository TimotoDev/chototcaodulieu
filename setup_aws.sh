#!/bin/bash
# setup_aws.sh — Deploy Chotot Lambda pipeline lên AWS
# DynamoDB only, không S3. Trigger: 00:00 VN (17:00 UTC) mỗi ngày.
#
# Usage: bash setup_aws.sh

set -euo pipefail

AWS="python3 -m awscli"
export AWS_PROFILE="${AWS_PROFILE:-harry}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-ap-southeast-1}"
REGION="$AWS_DEFAULT_REGION"

DDB_TABLE="chotot-xe-may"
LAMBDA_NAME="chotot-daily-scrape"
ROLE_NAME="chotot-lambda-role"
RULE_NAME="chotot-daily-0000"
LOG_GROUP="/aws/lambda/${LAMBDA_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "======================================================"
echo "  CHOTOT AWS SETUP"
echo "  Profile : $AWS_PROFILE"
echo "  Region  : $REGION"
echo "  Schedule: 00:00 VN = 17:00 UTC"
echo "======================================================"

# ── Lấy AWS Account ID ───────────────────────────────────────────────
ACCOUNT_ID=$($AWS sts get-caller-identity --query Account --output text)
echo "Account: $ACCOUNT_ID"

# ── 1. DynamoDB Table ─────────────────────────────────────────────────
echo ""
echo "[1/5] DynamoDB table: $DDB_TABLE"
if $AWS dynamodb describe-table --table-name "$DDB_TABLE" --region "$REGION" &>/dev/null; then
  echo "      → Đã tồn tại, bỏ qua."
else
  $AWS dynamodb create-table \
    --table-name "$DDB_TABLE" \
    --attribute-definitions AttributeName=list_id,AttributeType=N \
    --key-schema AttributeName=list_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$REGION"
  echo "      ✅ Tạo xong (PAY_PER_REQUEST)."
fi

# ── 2. IAM Role ──────────────────────────────────────────────────────
echo ""
echo "[2/5] IAM Role: $ROLE_NAME"
ROLE_ARN=$($AWS iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text 2>/dev/null || true)

if [ -z "$ROLE_ARN" ]; then
  TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  ROLE_ARN=$($AWS iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" \
    --query Role.Arn --output text)
  echo "      ✅ Role tạo xong: $ROLE_ARN"
else
  echo "      → Đã tồn tại: $ROLE_ARN"
fi

# Attach policies
$AWS iam attach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || true

# DynamoDB inline policy
DDB_POLICY=$(cat <<JSON
{
  "Version":"2012-10-17",
  "Statement":[{
    "Effect":"Allow",
    "Action":["dynamodb:PutItem","dynamodb:BatchWriteItem","dynamodb:GetItem","dynamodb:UpdateItem"],
    "Resource":"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${DDB_TABLE}"
  }]
}
JSON
)
$AWS iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "chotot-dynamodb" \
  --policy-document "$DDB_POLICY"

echo "      ✅ Policies attached."
echo "      Chờ role propagate (10s)..."
sleep 10

# ── 3. Lambda package ─────────────────────────────────────────────────
echo ""
echo "[3/5] Build Lambda ZIP"
TMPDIR_BUILD=$(mktemp -d)
ZIPFILE="$TMPDIR_BUILD/chotot_lambda.zip"

# Copy source files
cp "$SCRIPT_DIR/chotot.py"          "$TMPDIR_BUILD/"
cp "$SCRIPT_DIR/lambda_handler.py"  "$TMPDIR_BUILD/"

# Install dependencies
pip3 install -q -t "$TMPDIR_BUILD" requests boto3

# Zip
(cd "$TMPDIR_BUILD" && zip -r "$ZIPFILE" . -x "*.pyc" -x "__pycache__/*" -x "*.dist-info/*" -x "*.egg-info/*")

ZIP_SIZE_MB=$(du -m "$ZIPFILE" | cut -f1)
echo "      ZIP: ${ZIP_SIZE_MB} MB"

# ── 4. Deploy Lambda ──────────────────────────────────────────────────
echo ""
echo "[4/5] Lambda: $LAMBDA_NAME"
LAMBDA_ARN=$($AWS lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" --query Configuration.FunctionArn --output text 2>/dev/null || true)

ENV_VARS="Variables={CHOTOT_DDB_TABLE=${DDB_TABLE},AWS_DEFAULT_REGION=${REGION}}"

if [ -z "$LAMBDA_ARN" ]; then
  echo "      Tạo Lambda mới..."
  LAMBDA_ARN=$($AWS lambda create-function \
    --function-name "$LAMBDA_NAME" \
    --runtime python3.11 \
    --role "$ROLE_ARN" \
    --handler lambda_handler.handler \
    --zip-file "fileb://$ZIPFILE" \
    --timeout 900 \
    --memory-size 256 \
    --environment "$ENV_VARS" \
    --region "$REGION" \
    --query FunctionArn --output text)
  echo "      ✅ Tạo xong: $LAMBDA_ARN"
else
  echo "      Cập nhật Lambda code..."
  $AWS lambda update-function-code \
    --function-name "$LAMBDA_NAME" \
    --zip-file "fileb://$ZIPFILE" \
    --region "$REGION" > /dev/null
  sleep 5
  $AWS lambda update-function-configuration \
    --function-name "$LAMBDA_NAME" \
    --timeout 900 \
    --memory-size 256 \
    --environment "$ENV_VARS" \
    --region "$REGION" > /dev/null
  echo "      ✅ Cập nhật xong."
fi

# Cleanup
rm -rf "$TMPDIR_BUILD"

# ── 5. EventBridge Rule ───────────────────────────────────────────────
echo ""
echo "[5/5] EventBridge: $RULE_NAME (00:00 VN = 17:00 UTC)"

RULE_ARN=$($AWS events put-rule \
  --name "$RULE_NAME" \
  --schedule-expression "cron(0 17 * * ? *)" \
  --state ENABLED \
  --region "$REGION" \
  --query RuleArn --output text)
echo "      Rule ARN: $RULE_ARN"

# Permission cho EventBridge gọi Lambda
$AWS lambda add-permission \
  --function-name "$LAMBDA_NAME" \
  --statement-id "allow-eventbridge-daily" \
  --action "lambda:InvokeFunction" \
  --principal "events.amazonaws.com" \
  --source-arn "$RULE_ARN" \
  --region "$REGION" 2>/dev/null || echo "      (permission đã tồn tại)"

# Gắn Lambda vào Rule
$AWS events put-targets \
  --rule "$RULE_NAME" \
  --targets "Id=1,Arn=$LAMBDA_ARN" \
  --region "$REGION" > /dev/null
echo "      ✅ EventBridge → Lambda đã gắn."

# ── CloudWatch Log Group ──────────────────────────────────────────────
$AWS logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION" 2>/dev/null || true
$AWS logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 30 --region "$REGION" 2>/dev/null || true

# ── Summary ───────────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  SETUP XONG ✅"
echo ""
echo "  Lambda     : $LAMBDA_ARN"
echo "  DynamoDB   : $DDB_TABLE"
echo "  Schedule   : 00:00 VN (17:00 UTC) mỗi ngày"
echo "  Logs       : $LOG_GROUP"
echo ""
echo "  Test thủ công:"
echo "    python3 -m awscli lambda invoke \\"
echo "      --function-name $LAMBDA_NAME \\"
echo "      --payload '{\"dry_run\":true}' \\"
echo "      --region $REGION output.json && cat output.json"
echo "======================================================"
