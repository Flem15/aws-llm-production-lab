import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { isDeepStrictEqual } from 'node:util';
import './test-support';
import { matchesPartial } from './test-support';
import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { NetworkStack } from '../lib/network-stack';
import { EcrStack } from '../lib/ecr-stack';
import { EcsClusterStack } from '../lib/ecs-cluster-stack';
import { applyStandardTags } from '../lib/standard-tags';

const PROD_ACCOUNT = '123456789012';
const DEV_ACCOUNT = '655469962246';

function makeApp() {
  const app = new cdk.App();

  const commonProps = {
    env: {
      account: PROD_ACCOUNT,
      region: 'us-east-2',
    },
  };

  const network = new NetworkStack(
    app,
    'LlmParityProdNetworkStack',
    { ...commonProps, environment: 'prod' },
  );

  const ecr = new EcrStack(
    app,
    'LlmParityProdEcrStack',
    { ...commonProps, environment: 'prod' },
  );

  const cluster = new EcsClusterStack(
    app,
    'LlmParityProdClusterStack',
    {
      ...commonProps,
      environment: 'prod',
      vpc: network.vpc,
    },
  );

  applyStandardTags(app, 'prod');

  return { network, ecr, cluster };
}

describe('Stage B production isolation', () => {
  test('uses a separate production account', () => {
    const { network, ecr, cluster } = makeApp();

    for (const stack of [network, ecr, cluster]) {
      assert.strictEqual(stack.account, PROD_ACCOUNT);
      assert.notStrictEqual(stack.account, DEV_ACCOUNT);
      assert.strictEqual(stack.region, 'us-east-2');
    }
  });

  test('uses production VPC and DNS names', () => {
    const { network } = makeApp();
    const template = Template.fromStack(network);

    template.hasResourceProperties('AWS::EC2::VPC', {
      Tags: Match.arrayWith([
        { Key: 'Name', Value: 'llm-parity-prod-vpc' },
      ]),
    });

    template.hasResourceProperties(
      'AWS::ServiceDiscovery::PrivateDnsNamespace',
      { Name: 'prod.llm.internal' },
    );
  });

  test('production network has no NAT gateways', () => {
    const { network } = makeApp();

    Template.fromStack(network).resourceCountIs(
      'AWS::EC2::NatGateway',
      0,
    );
  });

  test('uses a separate retained production ECR repository', () => {
    const { ecr } = makeApp();
    const template = Template.fromStack(ecr);

    template.hasResourceProperties(
      'AWS::ECR::Repository',
      {
        RepositoryName: 'demo-inference-api-prod',
        ImageTagMutability: 'IMMUTABLE',
      },
    );

    const repositories = template.findResources(
      'AWS::ECR::Repository',
    );

    assert.strictEqual((Object.keys(repositories)).length, 1);

    const repository = Object.values(repositories)[0] as {
      DeletionPolicy?: string;
      UpdateReplacePolicy?: string;
    };

    assert.strictEqual(repository.DeletionPolicy, 'Retain');
    assert.strictEqual(repository.UpdateReplacePolicy, 'Retain');
  });

  test('uses a production ECS cluster name', () => {
    const { cluster } = makeApp();

    Template.fromStack(cluster).hasResourceProperties(
      'AWS::ECS::Cluster',
      { ClusterName: 'llm-parity-prod-cluster' },
    );
  });

  test('applies production environment tags', () => {
    const { network } = makeApp();

    Template.fromStack(network).hasResourceProperties(
      'AWS::EC2::VPC',
      {
        Tags: Match.arrayWith([
          { Key: 'Environment', Value: 'prod' },
        ]),
      },
    );
  });
});
