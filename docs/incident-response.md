# LLM Production Parity Lab — Incident Response Runbook

## Purpose

This runbook provides first-response guidance for operational incidents affecting the LLM production parity lab.

Primary areas covered:

- API Gateway failures and authorization errors
- ECS service and task failures
- Application Load Balancer target health
- Elevated API latency
- Elevated API 4xx and 5xx responses
- Elevated ALB target response time
- ECS CPU and memory alarms
- Failed ECS deployments
- Abnormal ECS task stops

## Alert Sources

Operational alerts may originate from:

- CloudWatch alarms
- EventBridge rules
- SNS incident notifications
- ECS deployment state changes
- ECS task state changes

Key monitored alarms include:

- llm-parity-ecs-high-cpu
- llm-parity-ecs-high-memory
- llm-parity-alb-unhealthy-target
- llm-parity-alb-5xx
- llm-parity-api-5xx
- llm-parity-api-high-latency
- llm-parity-api-4xx
- llm-parity-alb-high-target-response-time

## Severity Guidance

### High Severity

Examples:

- sustained API 5xx errors
- unhealthy ALB targets
- failed ECS deployments
- abnormal ECS task stops
- service unavailable during a release
- repeated application failures

Immediate action:

1. Check ECS service state.
2. Check running and stopped tasks.
3. Review ALB target health.
4. Review API Gateway and application logs.
5. Review the most recent release.
6. Roll back if the incident correlates with a deployment.

### Moderate Severity

Examples:

- elevated API 4xx errors
- transient high latency
- isolated client authorization failures
- nonpersistent resource-utilization alarms

Immediate action:

1. Determine whether traffic is expected.
2. Review API access logs.
3. Check whether 4xx responses are expected IAM denials.
4. Review recent releases and configuration changes.

## First Five Minutes

Check ECS service state:

    aws ecs describe-services \
      --cluster llm-parity-cluster \
      --services llm-parity-demo-inference-service \
      --region us-east-2 \
      --query "services[0].[status,desiredCount,runningCount,pendingCount]" \
      --output table

Check running tasks:

    aws ecs list-tasks \
      --cluster llm-parity-cluster \
      --service-name llm-parity-demo-inference-service \
      --desired-status RUNNING \
      --region us-east-2

Check stopped tasks:

    aws ecs list-tasks \
      --cluster llm-parity-cluster \
      --service-name llm-parity-demo-inference-service \
      --desired-status STOPPED \
      --region us-east-2

## Request Correlation

Use the API Gateway request ID to correlate requests across API and application logs.

Saved CloudWatch Logs Insights queries:

- llm-parity/api/request-by-id
- llm-parity/application/request-by-id
- llm-parity/application/recent-warnings-errors

Replace REPLACE_WITH_REQUEST_ID with the request ID being investigated.

## API Authorization

GET /health is intentionally unauthenticated.

POST /invoke requires AWS IAM authorization.

Expected behavior:

- unsigned POST /invoke requests are rejected
- SigV4-signed requests from an authorized principal succeed

An elevated API 4xx rate may include legitimate IAM authorization denials. Correlate the request ID with API Gateway and application logs before escalating.

## Release Validation

The immutable ECS release workflow validates:

- requested ECR image exists
- ECS reaches a stable state
- task definition uses the requested immutable image
- ALB target becomes healthy
- /health reports the requested release SHA
- unsigned /invoke is rejected
- SigV4-authenticated /invoke succeeds
- ECS returns to zero desired tasks after validation

## Rollback Guidance

If an incident begins immediately after deployment:

1. Review the release evidence artifact.
2. Identify the previously deployed ECS task definition.
3. Trigger the established rollback workflow or restore the previous task definition.
4. Verify ECS service stability.
5. Verify ALB target health.
6. Re-run API validation.
7. Return the ECS service to the intended resting desired count.

## Cost-Safe Resting State

Expected resting ECS state:

    desiredCount = 0
    runningCount = 0
    pendingCount = 0

Verify with:

    aws ecs describe-services \
      --cluster llm-parity-cluster \
      --services llm-parity-demo-inference-service \
      --region us-east-2 \
      --query "services[0].[status,desiredCount,runningCount,pendingCount]" \
      --output text

## Evidence to Preserve

For significant incidents, preserve:

- request ID
- UTC timestamp
- affected route
- HTTP status code
- ECS task definition ARN
- immutable image SHA
- ECS service events
- stopped-task reason
- ALB target health
- relevant API Gateway logs
- relevant application logs
- GitHub Actions run ID
- release or rollback evidence artifact

## Resolution Checklist

Before closing an incident:

- service state is understood
- ALB target health is normal
- API behavior is validated
- protected routes enforce IAM authorization
- application logs show no unexplained errors
- release SHA is known
- rollback is completed if required
- ECS is returned to the intended desired count
- relevant evidence is retained
