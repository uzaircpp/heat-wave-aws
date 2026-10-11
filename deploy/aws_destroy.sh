#!/usr/bin/env bash
# Delete everything aws_deploy.sh created (stops all charges). Asks before deleting.
set -uo pipefail
REGION="${REGION:-ap-south-1}"; APP=urbanshield; ENV_NAME=urbanshield-prod; DB_ID=urbanshield-db; SECRET=urbanshield/app
export AWS_DEFAULT_REGION="$REGION" AWS_REGION="$REGION" AWS_PAGER=""
ACCOUNT=$(aws sts get-caller-identity --query Account --output text); BUCKET="${APP}-${ACCOUNT}-${REGION}"
read -r -p "Delete the Rahat website, database (all reports) and photos in $REGION? Type DELETE: " A
[ "$A" = "DELETE" ] || { echo "Nothing deleted."; exit 0; }

DIST_ID=$(aws cloudfront list-distributions --query "DistributionList.Items[?Comment=='$APP'].Id | [0]" --output text 2>/dev/null)
if [ -n "$DIST_ID" ] && [ "$DIST_ID" != "None" ]; then
  echo "Disabling CloudFront $DIST_ID (takes ~10 minutes before it can be deleted)"
  aws cloudfront get-distribution-config --id "$DIST_ID" > /tmp/cfc.json
  ETAG=$(python3 -c "import json;print(json.load(open('/tmp/cfc.json'))['ETag'])")
  python3 -c "import json;d=json.load(open('/tmp/cfc.json'))['DistributionConfig'];d['Enabled']=False;json.dump(d,open('/tmp/cfd.json','w'))"
  aws cloudfront update-distribution --id "$DIST_ID" --if-match "$ETAG" --distribution-config file:///tmp/cfd.json >/dev/null
fi
echo "Terminating Elastic Beanstalk environment and app"
aws elasticbeanstalk terminate-environment --environment-name $ENV_NAME >/dev/null 2>&1
echo "Deleting database (no final snapshot)"
aws rds delete-db-instance --db-instance-identifier $DB_ID --skip-final-snapshot --delete-automated-backups >/dev/null 2>&1
echo "Deleting secret and photo bucket"
aws secretsmanager delete-secret --secret-id $SECRET --force-delete-without-recovery >/dev/null 2>&1
aws s3 rm "s3://$BUCKET" --recursive --only-show-errors; aws s3api delete-bucket --bucket "$BUCKET" 2>/dev/null
echo "Waiting for the environment and database to finish deleting"
aws elasticbeanstalk wait environment-terminated --environment-names $ENV_NAME 2>/dev/null
aws elasticbeanstalk delete-application --application-name $APP --terminate-env-by-force >/dev/null 2>&1
aws rds wait db-instance-deleted --db-instance-identifier $DB_ID 2>/dev/null
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
for n in ${APP}-db ${APP}-app; do
  G=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$n Name=vpc-id,Values=$VPC --query 'SecurityGroups[0].GroupId' --output text)
  [ "$G" != "None" ] && aws ec2 delete-security-group --group-id "$G" 2>/dev/null
done
for R in ${APP}-eb-ec2-role; do
  aws iam remove-role-from-instance-profile --instance-profile-name ${APP}-eb-ec2-profile --role-name $R 2>/dev/null
  aws iam delete-instance-profile --instance-profile-name ${APP}-eb-ec2-profile 2>/dev/null
  aws iam delete-role-policy --role-name $R --policy-name ${APP}-app 2>/dev/null
  aws iam detach-role-policy --role-name $R --policy-arn arn:aws:iam::aws:policy/AWSElasticBeanstalkWebTier 2>/dev/null
  aws iam delete-role --role-name $R 2>/dev/null
done
for P in service-role/AWSElasticBeanstalkEnhancedHealth AWSElasticBeanstalkManagedUpdatesCustomerRolePolicy; do
  aws iam detach-role-policy --role-name ${APP}-eb-service-role --policy-arn arn:aws:iam::aws:policy/$P 2>/dev/null; done
aws iam delete-role --role-name ${APP}-eb-service-role 2>/dev/null
if [ -n "${DIST_ID:-}" ] && [ "$DIST_ID" != "None" ]; then
  echo "Waiting for CloudFront to finish disabling, then deleting it"
  aws cloudfront wait distribution-deployed --id "$DIST_ID"
  ETAG=$(aws cloudfront get-distribution --id "$DIST_ID" --query ETag --output text)
  aws cloudfront delete-distribution --id "$DIST_ID" --if-match "$ETAG"
fi
echo "Done. Everything Rahat created in $REGION is deleted."
