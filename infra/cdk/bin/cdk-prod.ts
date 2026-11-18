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
import { CostGovernanceStack } from '../lib/cost-governance-stack';
import { IncidentResponseStack } from '../lib/incident-response-stack';
import { DiagnosticsStack } from '../lib/diagnostics-stack';
import { PrivateServiceEndpointsStack } from '../lib/private-service-endpoints-stack';
import { applyStandardTags } from '../lib/standard-tags';

if (process.env.LLM_PARITY_PROD_SYNTH_ONLY !== '1') {
  throw new Error(
    'Production CDK app requires LLM_PARITY_PROD_SYNTH_ONLY=1',
  );
}

const app = new cdk.App();

const config = resolveEnvironmentConfig({
  environment: 'prod',
  prodAccount: app.node.tryGetContext('prodAccount'),
  imageTag: app.node.tryGetContext('imageTag'),
  monthlyBudgetLimit: app.node.tryGetContext('monthlyBudgetLimit'),
  enableProductionDeployment:
    app.node.tryGetContext('enableProductionDeployment'),
});

if (config.deploymentEnabled) {
  throw new Error('Production deployment must remain disabled');
}

const stackProps = () => ({
  env: {
    account: config.account,
    region: config.region,
  },
  environment: config.name,
  synthesizer: new DefaultStackSynthesizer({
    qualifier: 'llmprod',
  }),
});

const ecrStack = new EcrStack(
  app,
  `${config.stackPrefix}EcrStack`,
  stackProps(),
);

// Resolve AZs in CloudFormation so a synth-only run needs no AWS lookup.
class ProductionNetworkStack extends NetworkStack {
  override get availabilityZones(): string[] {
    return [0, 1].map((index) => cdk.Fn.select(index, cdk.Fn.getAzs()));
  }
}

const networkStack = new ProductionNetworkStack(
  app,
  `${config.stackPrefix}NetworkStack`,
  stackProps(),
);

const clusterStack = new EcsClusterStack(
  app,
  `${config.stackPrefix}ClusterStack`,
  {
    ...stackProps(),
    vpc: networkStack.vpc,
  },
);

const fargateServiceStack = new FargateServiceStack(
  app,
  `${config.stackPrefix}FargateServiceStack`,
  {
    ...stackProps(),
    vpc: networkStack.vpc,
    cluster: clusterStack.cluster,
    repository: ecrStack.repository,
    imageTag: config.imageTag,
  },
);

const apiStack = new ApiStack(
  app,
  `${config.stackPrefix}ApiStack`,
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
  `${config.stackPrefix}ObservabilityStack`,
  {
    ...stackProps(),
    service: fargateServiceStack.service,
    loadBalancer: fargateServiceStack.loadBalancer,
    targetGroup: fargateServiceStack.targetGroup,
    httpApi: apiStack.httpApi,
  },
);

new CostGovernanceStack(
  app,
  `${config.stackPrefix}CostGovernanceStack`,
  {
    ...stackProps(),
    monthlyBudgetLimit: config.monthlyBudgetLimit,
  },
);

new IncidentResponseStack(
  app,
  `${config.stackPrefix}IncidentResponseStack`,
  {
    ...stackProps(),
    cluster: clusterStack.cluster,
  },
);

new DiagnosticsStack(
  app,
  `${config.stackPrefix}DiagnosticsStack`,
  {
    ...stackProps(),
    httpApi: apiStack.httpApi,
    loadBalancer: fargateServiceStack.loadBalancer,
  },
);

new PrivateServiceEndpointsStack(
  app,
  `${config.stackPrefix}PrivateServiceEndpointsStack`,
  {
    ...stackProps(),
    vpc: networkStack.vpc,
    serviceSecurityGroup:
      fargateServiceStack.serviceSecurityGroup,
  },
);

applyStandardTags(app, 'prod');
