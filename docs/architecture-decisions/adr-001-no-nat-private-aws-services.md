# ADR-001 — Remove NAT Gateway and Use Private AWS Service Endpoints

## Status

Accepted after controlled Day 13 validation.

## Context

The development environment previously used one NAT Gateway to provide outbound connectivity from private ECS/Fargate subnets.

The workload requires AWS-managed connectivity for:

- Amazon ECR API
- Amazon ECR Docker registry
- Amazon S3 image layers
- Amazon CloudWatch Logs

The workload does not currently require runtime internet access, Secrets Manager, SSM Parameter Store, or public third-party APIs.

## Decision

Remove the NAT Gateway and its Elastic IP.

Provide required AWS-service connectivity with:

- ECR API interface VPC endpoint
- ECR DKR interface VPC endpoint
- CloudWatch Logs interface VPC endpoint
- S3 gateway VPC endpoint

Keep private DNS enabled on the three interface endpoints.

Restrict interface endpoint HTTPS ingress to the ECS service security group.

## Technical Validation

The architecture was validated with the NAT Gateway absent.

The immutable release workflow successfully demonstrated:

- ECR image discovery
- Fargate image pull
- container startup
- CloudWatch log delivery
- ALB target health
- public API health validation
- AWS IAM / SigV4 authenticated invocation
- application negative request validation
- automatic return to ECS desired count zero

The ECS private subnets had no IPv4 internet default route during the test.

## Cost Analysis

Approximate fixed monthly cost using 730 hours:

### Previous NAT design

- NAT Gateway: approximately $32.85
- NAT Elastic/public IPv4: approximately $3.65
- fixed total: approximately $36.50
- NAT data processing billed separately

### Private endpoint design

Three interface endpoint services are deployed in two Availability Zones:

- 6 interface endpoint ENIs total
- approximately $43.80 fixed monthly cost
- interface endpoint data processing billed separately
- S3 gateway endpoint has no hourly endpoint charge

The private endpoint design therefore costs approximately $7.30 more per month in fixed network charges under these assumptions.

## Rationale

The project prioritizes production-parity isolation over absolute minimum development cost.

The no-NAT design demonstrates:

- no NAT dependency
- no general IPv4 internet egress from private ECS subnets
- private image retrieval
- private CloudWatch logging
- explicit AWS-service network dependencies
- smaller network egress attack surface

This architecture is therefore retained despite the modest increase in fixed cost.

## Consequences

Positive:

- stronger network isolation
- no NAT Gateway dependency
- no NAT Elastic IP
- AWS-service traffic remains private
- no NAT data-processing charges
- clear portfolio demonstration of AWS PrivateLink architecture

Negative:

- higher fixed cost for this low-traffic two-AZ lab
- additional VPC endpoints to operate
- new AWS-service dependencies may require additional endpoints in the future

## Future Requirement

Before introducing any service that requires public internet connectivity or another AWS API, explicitly determine whether a new VPC endpoint is required.

Examples include:

- AWS Secrets Manager
- AWS Systems Manager Parameter Store
- AWS KMS
- STS calls made directly by application code
- external AI/model APIs
- external SaaS APIs
