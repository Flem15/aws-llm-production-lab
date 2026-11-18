import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { isDeepStrictEqual } from 'node:util';
import './test-support';
import { matchesPartial } from './test-support';
import * as cdk from 'aws-cdk-lib';
import {
  Match,
  Template,
} from 'aws-cdk-lib/assertions';
import { EcrStack } from '../lib/ecr-stack';
import { NetworkStack } from '../lib/network-stack';
import { EcsClusterStack } from '../lib/ecs-cluster-stack';
import { FargateServiceStack } from '../lib/fargate-service-stack';
import { ApiStack } from '../lib/api-stack';
import { GitHubOidcStack } from '../lib/github-oidc-stack';

const immutableImageTag =
  '0123456789abcdef0123456789abcdef01234567';

interface SecurityTemplates {
  fargate: Template;
  api: Template;
  githubOidc: Template;
}

function createSecurityTemplates(): SecurityTemplates {
  const app = new cdk.App();

  const commonProps: cdk.StackProps = {
    env: {
      account: '111111111111',
      region: 'us-east-2',
    },
  };

  const ecrStack = new EcrStack(
    app,
    'SecurityTestEcrStack',
    commonProps,
  );

  const networkStack = new NetworkStack(
    app,
    'SecurityTestNetworkStack',
    commonProps,
  );

  const clusterStack = new EcsClusterStack(
    app,
    'SecurityTestClusterStack',
    {
      ...commonProps,
      vpc: networkStack.vpc,
    },
  );

  const fargateStack = new FargateServiceStack(
    app,
    'SecurityTestFargateStack',
    {
      ...commonProps,
      vpc: networkStack.vpc,
      cluster: clusterStack.cluster,
      repository: ecrStack.repository,
      imageTag: immutableImageTag,
    },
  );

  const apiStack = new ApiStack(
    app,
    'SecurityTestApiStack',
    {
      ...commonProps,
      vpc: networkStack.vpc,
      listener: fargateStack.listener,
      loadBalancerSecurityGroup:
        fargateStack.loadBalancerSecurityGroup,
    },
  );

  const githubOidcStack = new GitHubOidcStack(
    app,
    'SecurityTestGitHubOidcStack',
    {
      ...commonProps,
      repository: ecrStack.repository,
      httpApi: apiStack.httpApi,
    taskDefinition: fargateStack.taskDefinition,
    },
  );

  return {
    fargate: Template.fromStack(fargateStack),
    api: Template.fromStack(apiStack),
    githubOidc: Template.fromStack(
      githubOidcStack,
    ),
  };
}

describe('security boundaries', () => {
  const templates = createSecurityTemplates();

  test('GitHub OIDC role trusts only the dev environment in the exact repository', () => {
    templates.githubOidc.hasResourceProperties(
      'AWS::IAM::Role',
      {
        RoleName: 'llm-parity-actions-ecr-push',
        MaxSessionDuration: 3600,
        AssumeRolePolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Action:
                'sts:AssumeRoleWithWebIdentity',
              Effect: 'Allow',
              Principal: {
                Federated: Match.anyValue(),
              },
              Condition: {
                StringEquals: {
                  'token.actions.githubusercontent.com:aud':
                    'sts.amazonaws.com',
                  'token.actions.githubusercontent.com:sub':
                    'repo:Flem15/aws-llm-production-lab:environment:dev',
                },
              },
            }),
          ]),
        },
      },
    );
  });

  test('GitHub OIDC trust does not contain repository wildcards', () => {
    const roles =
      templates.githubOidc.findResources(
        'AWS::IAM::Role',
      );

    const serialized = JSON.stringify(roles);

    assert.ok(!(serialized).includes('repo:Flem15/*'));

    assert.ok(!(serialized).includes('repo:*/*'));
  });

  test('GitHub role scopes release writes to the dev ECS service', () => {
    const policies = templates.githubOidc.findResources('AWS::IAM::Policy');
    const statements = Object.values(policies).flatMap(
      (policy) => policy.Properties.PolicyDocument.Statement,
    );
    const releaseWrites = statements.filter(
      (statement) => [statement.Action].flat().includes('ecs:UpdateService'),
    );

    assert.strictEqual((releaseWrites).length, 1);
    const resource = JSON.stringify(releaseWrites[0].Resource);
    assert.ok((resource).includes(':ecs:us-east-2:111111111111:service/llm-parity-cluster/llm-parity-demo-inference-service'));
    assert.ok(!(resource).includes('*'));
  });

  test('release role cannot assume bootstrap roles or administer IAM', () => {
    const policies = templates.githubOidc.findResources('AWS::IAM::Policy');
    const statements = Object.values(policies).flatMap(
      (policy) => policy.Properties.PolicyDocument.Statement,
    );
    const actions = statements.filter((statement) => statement.Effect === 'Allow').flatMap((statement) => [statement.Action].flat());
    assert.ok(!(actions).includes('sts:AssumeRole'));
    assert.ok(!(actions).includes('iam:*'));
    assert.ok((actions).includes('ecs:RegisterTaskDefinition'));
    const passRole = statements.find((statement) => statement.Action === 'iam:PassRole');
    assert.ok(!(JSON.stringify(passRole.Resource)).includes('*'));
    assert.strictEqual(passRole.Condition.StringEquals['iam:PassedToService'], 'ecs-tasks.amazonaws.com');
  });

  test('API Gateway applies conservative default throttling', () => {
    templates.api.hasResourceProperties(
      'AWS::ApiGatewayV2::Stage',
      {
        StageName: '$default',
        DefaultRouteSettings: {
          ThrottlingBurstLimit: 20,
          ThrottlingRateLimit: 10,
        },
      },
    );
  });

  test('VPC Link security group reaches the private ALB only on HTTP port 80', () => {
    templates.api.hasResourceProperties(
      'AWS::EC2::SecurityGroupIngress',
      {
        IpProtocol: 'tcp',
        FromPort: 80,
        ToPort: 80,
        GroupId: Match.anyValue(),
        SourceSecurityGroupId: Match.anyValue(),
      },
    );
  });

  test('private ALB remains internal', () => {
    templates.fargate.hasResourceProperties(
      'AWS::ElasticLoadBalancingV2::LoadBalancer',
      {
        Name: 'llm-parity-private-alb',
        Scheme: 'internal',
        Type: 'application',
      },
    );
  });

  test('ECS tasks remain private without public IP assignment', () => {
    templates.fargate.hasResourceProperties(
      'AWS::ECS::Service',
      {
        NetworkConfiguration: {
          AwsvpcConfiguration: {
            AssignPublicIp: 'DISABLED',
            SecurityGroups: Match.anyValue(),
            Subnets: Match.anyValue(),
          },
        },
      },
    );
  });

  test('container image is pinned to the requested immutable SHA', () => {
    const taskDefinitions =
      templates.fargate.findResources(
        'AWS::ECS::TaskDefinition',
      );

    assert.strictEqual((Object.keys(taskDefinitions)).length, 1);

    const taskDefinition = Object.values(
      taskDefinitions,
    )[0] as {
      Properties: {
        ContainerDefinitions: Array<{
          Name: string;
          Image: unknown;
          Environment?: Array<{
            Name: string;
            Value: string;
          }>;
        }>;
      };
    };

    const container =
      taskDefinition.Properties.ContainerDefinitions.find(
        (definition) =>
          definition.Name === 'demo-inference-api',
      );

    assert.notStrictEqual(container, undefined);

    const imageExpression = JSON.stringify(
      container?.Image,
    );

    assert.ok((imageExpression).includes(immutableImageTag));

    {
assert.ok((container?.Environment ?? []).some((item: unknown) => isDeepStrictEqual(item, {
          Name: 'APP_VERSION',
          Value: immutableImageTag,
        })));
};
  });

  test('GitHub OIDC role can invoke only the protected inference route', () => {
    templates.githubOidc.hasResourceProperties(
      'AWS::IAM::Policy',
      {
        PolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Sid:
                'InvokeProtectedInferenceRoute',
              Effect: 'Allow',
              Action:
                'execute-api:Invoke',
              Resource:
                Match.anyValue(),
            }),
          ]),
        },
      },
    );

    const policies =
      templates.githubOidc.findResources(
        'AWS::IAM::Policy',
      );

    const serialized =
      JSON.stringify(policies);

    assert.ok((serialized).includes('$default/POST/invoke'));

    assert.ok(!(serialized).includes('$default/*/*'));

    assert.ok(!(serialized).includes('/*/POST/invoke'));

    assert.ok(!(serialized).includes('/*/*/*'));
  });


});

test('GitHub release explicitly denies bootstrap escalation even if another policy grants it', () => {
  const template = createSecurityTemplates().githubOidc;
  template.hasResourceProperties('AWS::IAM::Policy', { PolicyDocument: { Statement: Match.arrayWith([
    Match.objectLike({ Sid: 'DenyInfrastructureEscalation', Effect: 'Deny', Action: ['sts:AssumeRole', 'cloudformation:*'], Resource: '*' })
  ]) } });
});
