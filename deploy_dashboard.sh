#!/usr/bin/env bash
# deploy_dashboard.sh — Deploy Chotot Dashboard to AWS
# Usage: bash deploy_dashboard.sh
set -euo pipefail

PROFILE="harry"
REGION="ap-southeast-1"
ACCOUNT_ID="404850807717"
TABLE_NAME="chotot-xe-may"
LAMBDA_NAME="chotot-dashboard-api"
ROLE_NAME="chotot-api-role"
API_NAME="chotot-dashboard-api"
BUCKET_NAME="chotot-dashboard-${ACCOUNT_ID}"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── 1. Create IAM Role ─────────────────────────────────────────────
log "=== Step 1: IAM Role ==="

TRUST_POLICY='{
  "Version":"2012-10-17",
  "Statement":[{
    "Effect":"Allow",
    "Principal":{"Service":"lambda.amazonaws.com"},
    "Action":"sts:AssumeRole"
  }]
}'

ROLE_ARN=$(python3 -m awscli iam get-role --role-name "${ROLE_NAME}" --profile "${PROFILE}" --query 'Role.Arn' --output text 2>/dev/null || true)

if [ -z "${ROLE_ARN}" ]; then
  log "Creating IAM role ${ROLE_NAME}..."
  ROLE_ARN=$(python3 -m awscli iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "${TRUST_POLICY}" \
    --profile "${PROFILE}" \
    --query 'Role.Arn' --output text)
  log "Role created: ${ROLE_ARN}"
else
  log "Role already exists: ${ROLE_ARN}"
fi

# Attach policies
python3 -m awscli iam attach-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" \
  --profile "${PROFILE}" 2>/dev/null || true

python3 -m awscli iam attach-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-arn "arn:aws:iam::aws:policy/AmazonDynamoDBReadOnlyAccess" \
  --profile "${PROFILE}" 2>/dev/null || true

log "Waiting 10s for role propagation..."
sleep 10

# ── 2. Package Lambda ──────────────────────────────────────────────
log "=== Step 2: Package Lambda ==="
cd "${WORKDIR}"
rm -f /tmp/chotot_api_lambda.zip
zip -j /tmp/chotot_api_lambda.zip api_handler.py
log "Lambda zip created: /tmp/chotot_api_lambda.zip"

# ── 3. Deploy Lambda ───────────────────────────────────────────────
log "=== Step 3: Deploy Lambda ==="

LAMBDA_ARN=$(python3 -m awscli lambda get-function \
  --function-name "${LAMBDA_NAME}" \
  --region "${REGION}" \
  --profile "${PROFILE}" \
  --query 'Configuration.FunctionArn' --output text 2>/dev/null || true)

if [ -z "${LAMBDA_ARN}" ] || [ "${LAMBDA_ARN}" = "None" ]; then
  log "Creating Lambda function ${LAMBDA_NAME}..."
  LAMBDA_ARN=$(python3 -m awscli lambda create-function \
    --function-name "${LAMBDA_NAME}" \
    --runtime python3.12 \
    --role "${ROLE_ARN}" \
    --handler api_handler.handler \
    --zip-file fileb:///tmp/chotot_api_lambda.zip \
    --timeout 30 \
    --memory-size 512 \
    --environment "Variables={CHOTOT_DDB_TABLE=${TABLE_NAME},AWS_DEFAULT_REGION=${REGION}}" \
    --region "${REGION}" \
    --profile "${PROFILE}" \
    --query 'FunctionArn' --output text)
  log "Lambda created: ${LAMBDA_ARN}"
else
  log "Updating Lambda code..."
  python3 -m awscli lambda update-function-code \
    --function-name "${LAMBDA_NAME}" \
    --zip-file fileb:///tmp/chotot_api_lambda.zip \
    --region "${REGION}" \
    --profile "${PROFILE}" > /dev/null
  python3 -m awscli lambda update-function-configuration \
    --function-name "${LAMBDA_NAME}" \
    --timeout 30 \
    --memory-size 512 \
    --environment "Variables={CHOTOT_DDB_TABLE=${TABLE_NAME},AWS_DEFAULT_REGION=${REGION}}" \
    --region "${REGION}" \
    --profile "${PROFILE}" > /dev/null
  LAMBDA_ARN=$(python3 -m awscli lambda get-function \
    --function-name "${LAMBDA_NAME}" \
    --region "${REGION}" \
    --profile "${PROFILE}" \
    --query 'Configuration.FunctionArn' --output text)
  log "Lambda updated: ${LAMBDA_ARN}"
fi

# Wait for Lambda to be active
log "Waiting for Lambda to become active..."
python3 -m awscli lambda wait function-active \
  --function-name "${LAMBDA_NAME}" \
  --region "${REGION}" \
  --profile "${PROFILE}" 2>/dev/null || sleep 5

# ── 4. Create HTTP API Gateway ─────────────────────────────────────
log "=== Step 4: API Gateway ==="

# Check existing API
EXISTING_API_ID=$(python3 -m awscli apigatewayv2 get-apis \
  --region "${REGION}" \
  --profile "${PROFILE}" \
  --query "Items[?Name=='${API_NAME}'].ApiId | [0]" \
  --output text 2>/dev/null || true)

if [ -z "${EXISTING_API_ID}" ] || [ "${EXISTING_API_ID}" = "None" ]; then
  log "Creating HTTP API: ${API_NAME}..."
  EXISTING_API_ID=$(python3 -m awscli apigatewayv2 create-api \
    --name "${API_NAME}" \
    --protocol-type HTTP \
    --cors-configuration 'AllowOrigins=["*"],AllowMethods=["GET","OPTIONS"],AllowHeaders=["*"]' \
    --region "${REGION}" \
    --profile "${PROFILE}" \
    --query 'ApiId' --output text)
  log "API created: ${EXISTING_API_ID}"
else
  log "API already exists: ${EXISTING_API_ID}"
fi

API_ID="${EXISTING_API_ID}"
API_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com"

# Create Lambda integration
INTEGRATION_ID=$(python3 -m awscli apigatewayv2 get-integrations \
  --api-id "${API_ID}" \
  --region "${REGION}" \
  --profile "${PROFILE}" \
  --query "Items[?IntegrationUri=='${LAMBDA_ARN}'].IntegrationId | [0]" \
  --output text 2>/dev/null || true)

if [ -z "${INTEGRATION_ID}" ] || [ "${INTEGRATION_ID}" = "None" ]; then
  log "Creating Lambda integration..."
  INTEGRATION_ID=$(python3 -m awscli apigatewayv2 create-integration \
    --api-id "${API_ID}" \
    --integration-type AWS_PROXY \
    --integration-uri "${LAMBDA_ARN}" \
    --payload-format-version "2.0" \
    --region "${REGION}" \
    --profile "${PROFILE}" \
    --query 'IntegrationId' --output text)
  log "Integration created: ${INTEGRATION_ID}"
fi

# Create routes
for ROUTE_KEY in "GET /api/dates" "GET /api/listings" "GET /api/stats"; do
  EXISTING_ROUTE=$(python3 -m awscli apigatewayv2 get-routes \
    --api-id "${API_ID}" \
    --region "${REGION}" \
    --profile "${PROFILE}" \
    --query "Items[?RouteKey=='${ROUTE_KEY}'].RouteId | [0]" \
    --output text 2>/dev/null || true)

  if [ -z "${EXISTING_ROUTE}" ] || [ "${EXISTING_ROUTE}" = "None" ]; then
    python3 -m awscli apigatewayv2 create-route \
      --api-id "${API_ID}" \
      --route-key "${ROUTE_KEY}" \
      --target "integrations/${INTEGRATION_ID}" \
      --region "${REGION}" \
      --profile "${PROFILE}" > /dev/null
    log "Route created: ${ROUTE_KEY}"
  else
    log "Route exists: ${ROUTE_KEY}"
  fi
done

# Create or update $default stage with auto-deploy
STAGE_EXISTS=$(python3 -m awscli apigatewayv2 get-stages \
  --api-id "${API_ID}" \
  --region "${REGION}" \
  --profile "${PROFILE}" \
  --query "Items[?StageName=='\$default'].StageName | [0]" \
  --output text 2>/dev/null || true)

if [ -z "${STAGE_EXISTS}" ] || [ "${STAGE_EXISTS}" = "None" ]; then
  python3 -m awscli apigatewayv2 create-stage \
    --api-id "${API_ID}" \
    --stage-name '$default' \
    --auto-deploy \
    --region "${REGION}" \
    --profile "${PROFILE}" > /dev/null
  log "Stage created: \$default"
else
  log "Stage exists: \$default"
fi

# Grant API Gateway permission to invoke Lambda
python3 -m awscli lambda add-permission \
  --function-name "${LAMBDA_NAME}" \
  --statement-id "apigateway-invoke-${API_ID}" \
  --action "lambda:InvokeFunction" \
  --principal "apigateway.amazonaws.com" \
  --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*" \
  --region "${REGION}" \
  --profile "${PROFILE}" 2>/dev/null || log "Lambda permission already exists"

log "API Gateway URL: ${API_URL}"

# ── 5. Update index.html with API URL ─────────────────────────────
log "=== Step 5: Update index.html ==="
cd "${WORKDIR}"
sed -i.bak "s|API_GATEWAY_URL|${API_URL}|g" dashboard/index.html
log "index.html updated with API URL: ${API_URL}"

# ── 6. Create S3 Bucket ────────────────────────────────────────────
log "=== Step 6: S3 Static Hosting ==="

BUCKET_EXISTS=$(python3 -m awscli s3api head-bucket \
  --bucket "${BUCKET_NAME}" \
  --profile "${PROFILE}" \
  --region "${REGION}" 2>&1 || true)

if echo "${BUCKET_EXISTS}" | grep -q "404\|NoSuchBucket"; then
  log "Creating S3 bucket: ${BUCKET_NAME}..."
  python3 -m awscli s3api create-bucket \
    --bucket "${BUCKET_NAME}" \
    --region "${REGION}" \
    --create-bucket-configuration LocationConstraint="${REGION}" \
    --profile "${PROFILE}" > /dev/null
  log "Bucket created: ${BUCKET_NAME}"
else
  log "Bucket exists: ${BUCKET_NAME}"
fi

# Block public access (CloudFront will serve it)
python3 -m awscli s3api put-public-access-block \
  --bucket "${BUCKET_NAME}" \
  --public-access-block-configuration 'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true' \
  --profile "${PROFILE}" > /dev/null

# Upload index.html
python3 -m awscli s3 cp dashboard/index.html "s3://${BUCKET_NAME}/index.html" \
  --content-type "text/html; charset=utf-8" \
  --cache-control "no-cache" \
  --profile "${PROFILE}" > /dev/null
log "Uploaded index.html to s3://${BUCKET_NAME}/index.html"

# ── 7. CloudFront OAC ─────────────────────────────────────────────
log "=== Step 7: CloudFront ==="

# Check for existing CloudFront distribution
EXISTING_CF=$(python3 -m awscli cloudfront list-distributions \
  --profile "${PROFILE}" \
  --query "DistributionList.Items[?Comment=='chotot-dashboard'].Id | [0]" \
  --output text 2>/dev/null || true)

if [ -z "${EXISTING_CF}" ] || [ "${EXISTING_CF}" = "None" ]; then
  log "Creating CloudFront OAC..."
  OAC_ID=$(python3 -m awscli cloudfront create-origin-access-control \
    --origin-access-control-config "{
      \"Name\":\"chotot-dashboard-oac\",
      \"Description\":\"OAC for chotot dashboard\",
      \"SigningProtocol\":\"sigv4\",
      \"SigningBehavior\":\"always\",
      \"OriginAccessControlOriginType\":\"s3\"
    }" \
    --profile "${PROFILE}" \
    --query 'OriginAccessControl.Id' --output text 2>/dev/null || true)

  if [ -z "${OAC_ID}" ] || [ "${OAC_ID}" = "None" ]; then
    # Try to find existing OAC
    OAC_ID=$(python3 -m awscli cloudfront list-origin-access-controls \
      --profile "${PROFILE}" \
      --query "OriginAccessControlList.Items[?Name=='chotot-dashboard-oac'].Id | [0]" \
      --output text 2>/dev/null || true)
  fi

  CF_COMMENT="chotot-dashboard"
  CALLER_REF="chotot-dashboard-$(date +%s)"

  CF_CONFIG="{
    \"CallerReference\": \"${CALLER_REF}\",
    \"Comment\": \"${CF_COMMENT}\",
    \"DefaultCacheBehavior\": {
      \"TargetOriginId\": \"S3Origin\",
      \"ViewerProtocolPolicy\": \"redirect-to-https\",
      \"AllowedMethods\": {\"Quantity\": 2, \"Items\": [\"GET\", \"HEAD\"]},
      \"CachedMethods\": {\"Quantity\": 2, \"Items\": [\"GET\", \"HEAD\"]},
      \"Compress\": true,
      \"ForwardedValues\": {
        \"QueryString\": false,
        \"Cookies\": {\"Forward\": \"none\"}
      },
      \"MinTTL\": 0,
      \"DefaultTTL\": 86400,
      \"MaxTTL\": 31536000
    },
    \"Origins\": {
      \"Quantity\": 1,
      \"Items\": [{
        \"Id\": \"S3Origin\",
        \"DomainName\": \"${BUCKET_NAME}.s3.${REGION}.amazonaws.com\",
        \"S3OriginConfig\": {\"OriginAccessIdentity\": \"\"},
        \"OriginAccessControlId\": \"${OAC_ID}\"
      }]
    },
    \"DefaultRootObject\": \"index.html\",
    \"Enabled\": true,
    \"HttpVersion\": \"http2\",
    \"PriceClass\": \"PriceClass_200\"
  }"

  log "Creating CloudFront distribution..."
  CF_RESULT=$(python3 -m awscli cloudfront create-distribution \
    --distribution-config "${CF_CONFIG}" \
    --profile "${PROFILE}" 2>&1)
  CF_ID=$(echo "${CF_RESULT}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['Distribution']['Id'])" 2>/dev/null || true)
  CF_DOMAIN=$(echo "${CF_RESULT}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['Distribution']['DomainName'])" 2>/dev/null || true)

  if [ -n "${OAC_ID}" ] && [ "${OAC_ID}" != "None" ] && [ -n "${CF_ID}" ]; then
    # Update S3 bucket policy to allow CloudFront
    BUCKET_POLICY="{
      \"Version\": \"2012-10-17\",
      \"Statement\": [{
        \"Sid\": \"AllowCloudFrontServicePrincipal\",
        \"Effect\": \"Allow\",
        \"Principal\": {\"Service\": \"cloudfront.amazonaws.com\"},
        \"Action\": \"s3:GetObject\",
        \"Resource\": \"arn:aws:s3:::${BUCKET_NAME}/*\",
        \"Condition\": {\"StringEquals\": {\"AWS:SourceArn\": \"arn:aws:cloudfront::${ACCOUNT_ID}:distribution/${CF_ID}\"}}
      }]
    }"
    python3 -m awscli s3api put-bucket-policy \
      --bucket "${BUCKET_NAME}" \
      --policy "${BUCKET_POLICY}" \
      --profile "${PROFILE}" > /dev/null
    log "S3 bucket policy updated for CloudFront OAC"
  fi

else
  CF_ID="${EXISTING_CF}"
  CF_DOMAIN=$(python3 -m awscli cloudfront get-distribution \
    --id "${CF_ID}" \
    --profile "${PROFILE}" \
    --query 'Distribution.DomainName' --output text 2>/dev/null || true)
  log "CloudFront distribution already exists: ${CF_ID}"
fi

# ── 8. Summary ────────────────────────────────────────────────────
log ""
log "=========================================="
log "  DEPLOYMENT COMPLETE"
log "=========================================="
log ""
log "  Lambda Function:  ${LAMBDA_NAME}"
log "  API Gateway URL:  ${API_URL}"
log "  S3 Bucket:        s3://${BUCKET_NAME}"
if [ -n "${CF_DOMAIN:-}" ] && [ "${CF_DOMAIN}" != "None" ]; then
  log "  CloudFront URL:   https://${CF_DOMAIN}"
  log ""
  log "  Dashboard URL:    https://${CF_DOMAIN}"
fi
log ""
log "  Note: CloudFront may take 5-15 min to deploy globally."
log "=========================================="
