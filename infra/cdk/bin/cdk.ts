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

const app = new cdk.App();

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
  },
);
