#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { DefaultStackSynthesizer } from 'aws-cdk-lib';
import { EcrStack } from '../lib/ecr-stack';
import { NetworkStack } from '../lib/network-stack';
import { EcsClusterStack } from '../lib/ecs-cluster-stack';
import { FargateServiceStack } from '../lib/fargate-service-stack';
import { ApiStack } from '../lib/api-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { GitHubOidcStack } from '../lib/github-oidc-stack';
import { CostGovernanceStack } from '../lib/cost-governance-stack';
import { applyStandardTags } from '../lib/standard-tags';
import { IncidentResponseStack } from '../lib/incident-response-stack';
import { DiagnosticsStack } from '../lib/diagnostics-stack';
import { PrivateServiceEndpointsStack } from '../lib/private-service-endpoints-stack';

const app = new cdk.App();

const monthlyBudgetLimit = Number(
  app.node.tryGetContext('monthlyBudgetLimit') ??
    '100',
);

if (
  !Number.isFinite(monthlyBudgetLimit) ||
  monthlyBudgetLimit <= 0
) {
  throw new Error(
    'monthlyBudgetLimit must be a positive number',
  );
}


const imageTag =
  (app.node.tryGetContext('imageTag') as string | undefined) ??
  'v1';

const validImageTag =
  imageTag === 'v1' ||
  /^[0-9a-f]{40}$/.test(imageTag);

if (!validImageTag) {
  throw new Error(
    'imageTag must be v1 or a 40-character lowercase Git SHA',
  );
}

const commonProps: cdk.StackProps = {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-2',
  },
  synthesizer: new DefaultStackSynthesizer({
    qualifier: 'prod01',
  }),
};

const ecrStack = new EcrStack(
  app,
  'LlmParityDevEcrStack',
  commonProps,
);

const networkStack = new NetworkStack(
  app,
  'LlmParityDevNetworkStack',
  commonProps,
);

const clusterStack = new EcsClusterStack(
  app,
  'LlmParityDevClusterStack',
  {
    ...commonProps,
    vpc: networkStack.vpc,
  },
);

const fargateServiceStack = new FargateServiceStack(
  app,
  'LlmParityDevFargateServiceStack',
  {
    ...commonProps,
    vpc: networkStack.vpc,
    cluster: clusterStack.cluster,
    repository: ecrStack.repository,
    imageTag,
  },
);

const apiStack = new ApiStack(
  app,
  'LlmParityDevApiStack',
  {
    ...commonProps,
    vpc: networkStack.vpc,
    listener: fargateServiceStack.listener,
    loadBalancerSecurityGroup:
      fargateServiceStack.loadBalancerSecurityGroup,
  },
);

new ObservabilityStack(
  app,
  'LlmParityDevObservabilityStack',
  {
    ...commonProps,
    service: fargateServiceStack.service,
    loadBalancer: fargateServiceStack.loadBalancer,
    targetGroup: fargateServiceStack.targetGroup,
    httpApi: apiStack.httpApi,
  },
);

new GitHubOidcStack(
  app,
  'LlmParityDevGitHubOidcStack',
  {
    ...commonProps,
    repository: ecrStack.repository,
    httpApi: apiStack.httpApi,
  },
);


new CostGovernanceStack(
  app,
  'LlmParityDevCostGovernanceStack',
  {
    ...commonProps,
    monthlyBudgetLimit,
  },
);


new IncidentResponseStack(
  app,
  'LlmParityDevIncidentResponseStack',
  {
    ...commonProps,
    cluster: clusterStack.cluster,
  },
);


new DiagnosticsStack(
  app,
  'LlmParityDevDiagnosticsStack',
  {
    ...commonProps,
    httpApi:
      apiStack.httpApi,
    loadBalancer:
      fargateServiceStack.loadBalancer,
  },
);


new PrivateServiceEndpointsStack(
  app,
  'LlmParityDevPrivateServiceEndpointsStack',
  {
    ...commonProps,
    vpc:
      networkStack.vpc,
    serviceSecurityGroup:
      fargateServiceStack.serviceSecurityGroup,
  },
);

applyStandardTags(app);
