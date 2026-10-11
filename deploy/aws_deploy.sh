#!/usr/bin/env bash
# Rahat: deploy everything to AWS from AWS CloudShell (no local tools needed).
#
#   git clone https://github.com/uzaircpp/heat-wave-aws.git && cd heat-wave-aws && bash deploy/aws_deploy.sh
#
# Creates, in one region (default ap-south-1, Mumbai):
#   S3 bucket (report photos + deploy bundles), RDS PostgreSQL, Secrets Manager secret,
#   IAM roles, Elastic Beanstalk (Docker, single instance) with CloudWatch logs, CloudFront (HTTPS).
# The app then uses Rekognition, Translate, Bedrock, Polly and Amazon Location Service through its IAM role.
# Safe to run again: existing pieces are reused and the app is updated to the new code.
# Remove everything with: bash deploy/aws_destroy.sh
set -euo pipefail

REGION="${REGION:-ap-south-1}"
APP="urbanshield"
ENV_NAME="urbanshield-prod"
DB_ID="urbanshield-db"
SECRET="urbanshield/app"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.small}"
# AWS_REGION (set by CloudShell to the console region) wins over AWS_DEFAULT_REGION, so set both
export AWS_DEFAULT_REGION="$REGION" AWS_REGION="$REGION" AWS_PAGER=""

say()  { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[0;32m%s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mStopped: %s\033[0m\n' "$*"; exit 1; }
cd "$(dirname "$0")/.."
[ -f data/wards.json ] || die "data/wards.json is missing. Unzip the bundle that includes the prepared Delhi data."
[ -f Dockerfile ] || die "Run this from the project folder (heat-wave-aws)."

ACCOUNT=$(aws sts get-caller-identity --query Account --output text) || die "AWS credentials not working. Open this from AWS CloudShell."
BUCKET="${APP}-${ACCOUNT}-${REGION}"
TS=$(date +%Y%m%d-%H%M%S)
say "Account $ACCOUNT, region $REGION"

# ---------- S3 ----------
say "S3 bucket for photos and deploy bundles: $BUCKET"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  if [ "$REGION" = "us-east-1" ]; then aws s3api create-bucket --bucket "$BUCKET" >/dev/null
  else aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null; fi
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  ok "created (private, encrypted)"
else ok "exists"; fi

# ---------- network ----------
say "Network (default VPC) and security groups"
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
[ "$VPC" != "None" ] || die "This region has no default VPC. Create one: VPC console > Actions > Create default VPC."
sg() { aws ec2 describe-security-groups --filters Name=group-name,Values="$1" Name=vpc-id,Values="$VPC" --query 'SecurityGroups[0].GroupId' --output text; }
APP_SG=$(sg ${APP}-app)
[ "$APP_SG" != "None" ] || APP_SG=$(aws ec2 create-security-group --group-name ${APP}-app --description "Rahat web server" --vpc-id "$VPC" --query GroupId --output text)
DB_SG=$(sg ${APP}-db)
[ "$DB_SG" != "None" ] || DB_SG=$(aws ec2 create-security-group --group-name ${APP}-db --description "Rahat database, reachable only from the web server" --vpc-id "$VPC" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id "$DB_SG" --protocol tcp --port 5432 --source-group "$APP_SG" >/dev/null 2>&1 || true
ok "web $APP_SG, database $DB_SG (port 5432 only from the web server)"

# ---------- secrets ----------
say "Secrets Manager: $SECRET"
pyjson() { python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get(sys.argv[2],''))" "$1" "$2"; }
rand() { python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range($1)))"; }
if CUR=$(aws secretsmanager get-secret-value --secret-id "$SECRET" --query SecretString --output text 2>/dev/null); then
  DB_PW=$(pyjson "$CUR" DB_PASSWORD); OFFICER_TOKEN=$(pyjson "$CUR" OFFICER_TOKEN); ok "exists"
else
  DB_PW=$(rand 28); OFFICER_TOKEN=$(rand 32)
  aws secretsmanager create-secret --name "$SECRET" --description "Rahat app settings" \
    --secret-string "$(python3 -c "import json,sys; print(json.dumps({'DB_PASSWORD':sys.argv[1],'OFFICER_TOKEN':sys.argv[2]}))" "$DB_PW" "$OFFICER_TOKEN")" >/dev/null
  ok "created with a new database password and officer token"
fi
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET" --query ARN --output text)

# ---------- RDS ----------
say "RDS PostgreSQL: $DB_ID"
if aws rds describe-db-instances --db-instance-identifier "$DB_ID" >/dev/null 2>&1; then ok "exists"
else
  aws rds create-db-instance --db-instance-identifier "$DB_ID" --engine postgres --db-instance-class db.t4g.micro \
    --allocated-storage 20 --storage-type gp3 --master-username urbanshield --master-user-password "$DB_PW" \
    --db-name urbanshield --vpc-security-group-ids "$DB_SG" --no-publicly-accessible --backup-retention-period 1 \
    --storage-encrypted --no-multi-az --no-deletion-protection >/dev/null
  ok "creating (takes about 10 minutes; carrying on meanwhile)"
fi

# ---------- IAM ----------
say "IAM roles"
cat > /tmp/ec2-trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
cat > /tmp/eb-trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"elasticbeanstalk.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
cat > /tmp/app-policy.json <<JSON
{"Version":"2012-10-17","Statement":[
 {"Sid":"Photos","Effect":"Allow","Action":["s3:PutObject","s3:GetObject"],"Resource":"arn:aws:s3:::${BUCKET}/photos/*"},
 {"Sid":"Secret","Effect":"Allow","Action":"secretsmanager:GetSecretValue","Resource":"${SECRET_ARN}"},
 {"Sid":"Rekognition","Effect":"Allow","Action":["rekognition:DetectLabels","rekognition:DetectModerationLabels"],"Resource":"*"},
 {"Sid":"Translate","Effect":"Allow","Action":"translate:TranslateText","Resource":"*"},
 {"Sid":"Polly","Effect":"Allow","Action":"polly:SynthesizeSpeech","Resource":"*"},
 {"Sid":"Bedrock","Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":"*"},
 {"Sid":"Location","Effect":"Allow","Action":["geo-routes:CalculateRoutes","geo-places:Geocode","geo-places:ReverseGeocode","geo-places:SearchText"],"Resource":"*"}
]}
JSON
ROLE=${APP}-eb-ec2-role; PROFILE=${APP}-eb-ec2-profile; SROLE=${APP}-eb-service-role
aws iam get-role --role-name $ROLE >/dev/null 2>&1 || aws iam create-role --role-name $ROLE --assume-role-policy-document file:///tmp/ec2-trust.json >/dev/null
aws iam attach-role-policy --role-name $ROLE --policy-arn arn:aws:iam::aws:policy/AWSElasticBeanstalkWebTier
aws iam put-role-policy --role-name $ROLE --policy-name ${APP}-app --policy-document file:///tmp/app-policy.json
if ! aws iam get-instance-profile --instance-profile-name $PROFILE >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name $PROFILE >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name $PROFILE --role-name $ROLE
  sleep 10  # IAM takes a moment to propagate
fi
aws iam get-role --role-name $SROLE >/dev/null 2>&1 || aws iam create-role --role-name $SROLE --assume-role-policy-document file:///tmp/eb-trust.json >/dev/null
aws iam attach-role-policy --role-name $SROLE --policy-arn arn:aws:iam::aws:policy/service-role/AWSElasticBeanstalkEnhancedHealth
aws iam attach-role-policy --role-name $SROLE --policy-arn arn:aws:iam::aws:policy/AWSElasticBeanstalkManagedUpdatesCustomerRolePolicy
ok "server role can use: its S3 photo folder, its secret, Rekognition, Translate, Polly, Bedrock, Location Service"

# ---------- Bedrock model ----------
say "Amazon Bedrock: finding a model this account can use"
FEATURES="rekognition,translate,location,polly"
BEDROCK_MODEL=""
# Try the newest models first; Amazon Nova needs no use-case form, so it works where Anthropic models are not enabled.
# Also tries every inference profile this account can see in this region.
PROFILES=$(aws bedrock list-inference-profiles --query 'inferenceProfileSummaries[].inferenceProfileId' --output text 2>/dev/null | tr '\t' '\n' \
           | grep -Ei 'haiku-4|sonnet-4|nova' | sort -r || true)
: > /tmp/bedrock.log
for m in "${BEDROCK_MODEL_ID:-}" "global.anthropic.claude-haiku-4-5-20251001-v1:0" "au.anthropic.claude-haiku-4-5-20251001-v1:0" \
         "apac.anthropic.claude-haiku-4-5-20251001-v1:0" "us.anthropic.claude-haiku-4-5-20251001-v1:0" \
         "au.anthropic.claude-sonnet-4-5-20250929-v1:0" "global.anthropic.claude-sonnet-4-5-20250929-v1:0" \
         $PROFILES \
         "global.amazon.nova-2-lite-v1:0" "apac.amazon.nova-2-lite-v1:0" "apac.amazon.nova-lite-v1:0" "apac.amazon.nova-pro-v1:0" \
         "apac.amazon.nova-micro-v1:0" "amazon.nova-lite-v1:0" "amazon.nova-pro-v1:0" "amazon.nova-micro-v1:0" \
         "us.amazon.nova-lite-v1:0" "eu.amazon.nova-lite-v1:0"; do
  [ -n "$m" ] || continue
  if aws bedrock-runtime converse --model-id "$m" --messages '[{"role":"user","content":[{"text":"Reply with OK"}]}]' \
       --inference-config '{"maxTokens":5}' >/dev/null 2>/tmp/bedrock.err; then BEDROCK_MODEL="$m"; break; fi
  printf '%s: %s\n' "$m" "$(tr '\n' ' ' < /tmp/bedrock.err | tail -c 220)" >> /tmp/bedrock.log
done
if [ -n "$BEDROCK_MODEL" ]; then FEATURES="$FEATURES,bedrock"; ok "using $BEDROCK_MODEL"
else ok "no model answered. The chat uses its built-in assistant. Why each model failed (first lines):"
     head -n 6 /tmp/bedrock.log | sed 's/^/      /'
     ok "Enable a model in the Bedrock console (Model catalog), then run this script again."; fi

# ---------- satellite tree cover (runs here, next to the data on AWS) ----------
if ! python3 -c "import json,sys; w=json.load(open('data/wards.json')); sys.exit(0 if all(x.get('tree_frac') is not None for x in w) else 1)" 2>/dev/null; then
  say "Satellite tree cover (ESA WorldCover on AWS Open Data) for every ward"
  PY=$(command -v python3.12 || command -v python3.11 || true)
  if [ -z "$PY" ]; then sudo dnf install -y -q python3.11 python3.11-pip >/dev/null 2>&1 && PY=python3.11 || PY=python3; fi
  if $PY -m pip install --user -q -r requirements.txt 2>/tmp/pip.err && $PY scripts/prepare_data.py --landcover-only; then
    ok "wards updated with tree, green and built-up cover"
  else
    ok "skipped ($(tail -c 200 /tmp/pip.err)); the app still runs, with less detailed heat adjustments"
  fi
fi

# ---------- bundle ----------
say "Packaging the app"
BUNDLE="/tmp/${APP}-${TS}.zip"
python3 - "$BUNDLE" <<'PY'
import os, sys, zipfile
out = sys.argv[1]
skip_dirs = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "tests", "deploy", "uploads", "cache", "node_modules"}
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for d, dirs, files in os.walk("."):
        dirs[:] = [x for x in dirs if x not in skip_dirs]
        for f in files:
            if f == ".env" or f.endswith((".db", ".db-wal", ".db-shm", ".zip")):
                continue
            p = os.path.join(d, f)
            z.write(p, os.path.relpath(p, "."))
print(f"    {os.path.getsize(out) / 1e6:.1f} MB")
PY
aws s3 cp "$BUNDLE" "s3://$BUCKET/deploy/$(basename "$BUNDLE")" --only-show-errors
aws elasticbeanstalk describe-applications --application-names $APP --query 'Applications[0].ApplicationName' --output text | grep -q $APP \
  || aws elasticbeanstalk create-application --application-name $APP --description "Rahat heat and flood early warning" >/dev/null
VERSION="v-$TS"
aws elasticbeanstalk create-application-version --application-name $APP --version-label "$VERSION" \
  --source-bundle S3Bucket="$BUCKET",S3Key="deploy/$(basename "$BUNDLE")" >/dev/null
ok "version $VERSION uploaded"

# ---------- wait for the database, store its URL ----------
say "Waiting for the database to be ready (up to 20 minutes on the first run)"
aws rds wait db-instance-available --db-instance-identifier "$DB_ID"
DB_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB_ID" --query 'DBInstances[0].Endpoint.Address' --output text)
DATABASE_URL="postgresql+psycopg://urbanshield:${DB_PW}@${DB_HOST}:5432/urbanshield?sslmode=require"
aws secretsmanager put-secret-value --secret-id "$SECRET" --secret-string "$(python3 -c "import json,sys; print(json.dumps({'DB_PASSWORD':sys.argv[1],'OFFICER_TOKEN':sys.argv[2],'DATABASE_URL':sys.argv[3]}))" "$DB_PW" "$OFFICER_TOKEN" "$DATABASE_URL")" >/dev/null
ok "database ready at $DB_HOST (address stored in the secret)"

# ---------- Elastic Beanstalk ----------
say "Elastic Beanstalk environment: $ENV_NAME"
STACK=$(aws elasticbeanstalk list-available-solution-stacks --query "SolutionStacks[?contains(@, 'Amazon Linux 2023') && contains(@, 'running Docker')] | [0]" --output text)
[ "$STACK" != "None" ] || die "Could not find the Docker platform for Elastic Beanstalk in $REGION."
# Put the server in the default VPC explicitly (some accounts reject the security group otherwise).
SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" Name=default-for-az,Values=true \
          --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
[ -n "$SUBNETS" ] || SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
cat > /tmp/eb-options.json <<JSON
[
 {"Namespace":"aws:ec2:vpc","OptionName":"VPCId","Value":"$VPC"},
 {"Namespace":"aws:ec2:vpc","OptionName":"Subnets","Value":"$SUBNETS"},
 {"Namespace":"aws:ec2:vpc","OptionName":"AssociatePublicIpAddress","Value":"true"},
 {"Namespace":"aws:elasticbeanstalk:environment","OptionName":"EnvironmentType","Value":"SingleInstance"},
 {"Namespace":"aws:elasticbeanstalk:environment","OptionName":"ServiceRole","Value":"$SROLE"},
 {"Namespace":"aws:autoscaling:launchconfiguration","OptionName":"IamInstanceProfile","Value":"$PROFILE"},
 {"Namespace":"aws:autoscaling:launchconfiguration","OptionName":"SecurityGroups","Value":"$APP_SG"},
 {"Namespace":"aws:autoscaling:launchconfiguration","OptionName":"DisableIMDSv1","Value":"true"},
 {"Namespace":"aws:autoscaling:launchconfiguration","OptionName":"RootVolumeType","Value":"gp3"},
 {"Namespace":"aws:ec2:instances","OptionName":"InstanceTypes","Value":"$INSTANCE_TYPE"},
 {"Namespace":"aws:elasticbeanstalk:healthreporting:system","OptionName":"SystemType","Value":"enhanced"},
 {"Namespace":"aws:elasticbeanstalk:cloudwatch:logs","OptionName":"StreamLogs","Value":"true"},
 {"Namespace":"aws:elasticbeanstalk:cloudwatch:logs","OptionName":"RetentionInDays","Value":"7"},
 {"Namespace":"aws:elasticbeanstalk:application:environment","OptionName":"APP_AWS_REGION","Value":"$REGION"},
 {"Namespace":"aws:elasticbeanstalk:application:environment","OptionName":"APP_SECRET_ID","Value":"$SECRET"},
 {"Namespace":"aws:elasticbeanstalk:application:environment","OptionName":"APP_FEATURES","Value":"$FEATURES"},
 {"Namespace":"aws:elasticbeanstalk:application:environment","OptionName":"PHOTO_BUCKET","Value":"$BUCKET"},
 {"Namespace":"aws:elasticbeanstalk:application:environment","OptionName":"BEDROCK_MODEL_ID","Value":"${BEDROCK_MODEL:-none}"},
 {"Namespace":"aws:elasticbeanstalk:application:environment","OptionName":"CONTACT_EMAIL","Value":"${CONTACT_EMAIL:-}"}
]
JSON
STATUS=$(aws elasticbeanstalk describe-environments --application-name $APP --environment-names $ENV_NAME --no-include-deleted --query 'Environments[0].Status' --output text)
if [ "$STATUS" = "None" ]; then
  aws elasticbeanstalk create-environment --application-name $APP --environment-name $ENV_NAME --solution-stack-name "$STACK" \
    --version-label "$VERSION" --option-settings file:///tmp/eb-options.json >/dev/null
  ok "creating on $STACK"
else
  aws elasticbeanstalk update-environment --environment-name $ENV_NAME --version-label "$VERSION" --option-settings file:///tmp/eb-options.json >/dev/null
  ok "updating to $VERSION"
fi
printf '    building the container on AWS and starting it (5-10 minutes)'
for i in $(seq 1 90); do
  sleep 20
  read -r STATUS HEALTH CNAME < <(aws elasticbeanstalk describe-environments --application-name $APP --environment-names $ENV_NAME --no-include-deleted \
      --query 'Environments[0].[Status,Health,CNAME]' --output text)
  printf '.'
  [ "$STATUS" = "Ready" ] && break
done
echo
[ "$STATUS" = "Ready" ] || die "the environment did not become ready. Check: Elastic Beanstalk console > $ENV_NAME > Events and Logs."
ok "ready, health $HEALTH, server $CNAME"
if curl -fsS --max-time 20 "http://$CNAME/api/health" >/tmp/health.json; then ok "health check: $(cat /tmp/health.json)"
else ok "the server did not answer /api/health yet. Look at Elastic Beanstalk > Logs if this lasts more than a few minutes."; fi

# ---------- CloudFront ----------
say "CloudFront (HTTPS link)"
DIST_ID=$(aws cloudfront list-distributions --query "DistributionList.Items[?Comment=='$APP'].Id | [0]" --output text 2>/dev/null || echo None)
if [ "$DIST_ID" = "None" ] || [ -z "$DIST_ID" ]; then
  cat > /tmp/cf.json <<JSON
{"CallerReference":"$APP-$TS","Comment":"$APP","Enabled":true,"PriceClass":"PriceClass_200","HttpVersion":"http2and3",
 "Origins":{"Quantity":1,"Items":[{"Id":"eb","DomainName":"$CNAME",
   "CustomOriginConfig":{"HTTPPort":80,"HTTPSPort":443,"OriginProtocolPolicy":"http-only","OriginReadTimeout":60,"OriginKeepaliveTimeout":60,
     "OriginSslProtocols":{"Quantity":1,"Items":["TLSv1.2"]}}}]},
 "DefaultCacheBehavior":{"TargetOriginId":"eb","ViewerProtocolPolicy":"redirect-to-https","Compress":true,
   "AllowedMethods":{"Quantity":7,"Items":["GET","HEAD","OPTIONS","PUT","POST","PATCH","DELETE"],"CachedMethods":{"Quantity":2,"Items":["GET","HEAD"]}},
   "CachePolicyId":"4135ea2d-6df8-44a3-9df3-4b5a84be39ad","OriginRequestPolicyId":"216adef6-5c7f-47e4-b989-5492eafa07d3"}}
JSON
  DIST_ID=$(aws cloudfront create-distribution --distribution-config file:///tmp/cf.json --query Distribution.Id --output text)
  ok "created $DIST_ID (goes live worldwide in about 5-10 minutes)"
else
  ok "exists: $DIST_ID"
fi
DOMAIN=$(aws cloudfront get-distribution --id "$DIST_ID" --query Distribution.DomainName --output text)

cat <<EOF

=====================================================================
 Rahat is deployed.

 Website (HTTPS):  https://$DOMAIN
                   (if it does not open yet, wait 5-10 minutes for CloudFront)
 Direct server:    http://$CNAME
 Officer token:    $OFFICER_TOKEN
                   (Ward ops tab asks for it once; it is also in Secrets Manager: $SECRET)

 AWS in use: Elastic Beanstalk, EC2, RDS PostgreSQL, S3, CloudFront, Secrets Manager,
             CloudWatch Logs, IAM, Rekognition, Translate, Polly, Location Service${BEDROCK_MODEL:+, Bedrock ($BEDROCK_MODEL)}
 MCP server:       https://$DOMAIN/mcp

 Update after code changes:  bash deploy/aws_deploy.sh
 Live server logs:           bash deploy/aws_logs.sh
 Delete everything:          bash deploy/aws_destroy.sh
=====================================================================
EOF
