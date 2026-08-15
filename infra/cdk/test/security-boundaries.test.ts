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

    expect(serialized).not.toContain(
      'repo:Flem15/*',
    );

    expect(serialized).not.toContain(
      'repo:*/*',
    );
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

    expect(Object.keys(taskDefinitions)).toHaveLength(1);

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

    expect(container).toBeDefined();

    const imageExpression = JSON.stringify(
      container?.Image,
    );

    expect(imageExpression).toContain(
      immutableImageTag,
    );

    expect(container?.Environment).toEqual(
      expect.arrayContaining([
        {
          Name: 'APP_VERSION',
          Value: immutableImageTag,
        },
      ]),
    );
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

    expect(serialized).toContain(
      '$default/POST/invoke',
    );

    expect(serialized).not.toContain(
      '$default/*/*',
    );

    expect(serialized).not.toContain(
      '/*/POST/invoke',
    );

    expect(serialized).not.toContain(
      '/*/*/*',
    );
  });


});
