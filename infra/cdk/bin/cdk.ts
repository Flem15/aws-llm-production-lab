#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { DefaultStackSynthesizer } from 'aws-cdk-lib';
import { resolveEnvironmentConfig } from '../lib/environment-config';
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

const config = resolveEnvironmentConfig({
  devAccount: process.env.CDK_DEFAULT_ACCOUNT,
  imageTag: app.node.tryGetContext('imageTag'),
  monthlyBudgetLimit: app.node.tryGetContext('monthlyBudgetLimit'),
});
const { imageTag, monthlyBudgetLimit } = config;

const stackProps = (): cdk.StackProps => ({
  env: { account: config.account, region: config.region },
  synthesizer: new DefaultStackSynthesizer({
    qualifier: 'prod01',
  }),
});

const ecrStack = new EcrStack(
  app,
  'LlmParityDevEcrStack',
  stackProps(),
);

const networkStack = new NetworkStack(
  app,
  'LlmParityDevNetworkStack',
  stackProps(),
);

const clusterStack = new EcsClusterStack(
  app,
  'LlmParityDevClusterStack',
  {
    ...stackProps(),
    vpc: networkStack.vpc,
  },
);

const fargateServiceStack = new FargateServiceStack(
  app,
  'LlmParityDevFargateServiceStack',
  {
    ...stackProps(),
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
    ...stackProps(),
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
    ...stackProps(),
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
    ...stackProps(),
    repository: ecrStack.repository,
    httpApi: apiStack.httpApi,
    taskDefinition: fargateServiceStack.taskDefinition,
  },
);


new CostGovernanceStack(
  app,
  'LlmParityDevCostGovernanceStack',
  {
    ...stackProps(),
    monthlyBudgetLimit,
  },
);


new IncidentResponseStack(
  app,
  'LlmParityDevIncidentResponseStack',
  {
    ...stackProps(),
    cluster: clusterStack.cluster,
  },
);


new DiagnosticsStack(
  app,
  'LlmParityDevDiagnosticsStack',
  {
    ...stackProps(),
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
    ...stackProps(),
    vpc:
      networkStack.vpc,
    serviceSecurityGroup:
      fargateServiceStack.serviceSecurityGroup,
  },
);

applyStandardTags(app);
