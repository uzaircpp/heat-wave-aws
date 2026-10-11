#!/usr/bin/env bash
# Follow the Rahat server log live (CloudWatch Logs). Ctrl+C to stop.
export AWS_DEFAULT_REGION="${REGION:-ap-south-1}" AWS_REGION="${REGION:-ap-south-1}" AWS_PAGER=""
G=$(aws logs describe-log-groups --log-group-name-prefix /aws/elasticbeanstalk/urbanshield-prod \
    --query "logGroups[?contains(logGroupName,'stdouterr') || contains(logGroupName,'eb-docker')].logGroupName | [0]" --output text)
[ "$G" != "None" ] || { echo "No log group yet; the environment may still be starting."; exit 1; }
echo "Following $G"
aws logs tail "$G" --follow --since 30m
