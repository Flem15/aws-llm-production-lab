import * as cdk from 'aws-cdk-lib';
import {
  Match,
  Template,
} from 'aws-cdk-lib/assertions';
import { EcrStack } from '../lib/ecr-stack';
import { NetworkStack } from '../lib/network-stack';
import { EcsClusterStack } from '../lib/ecs-cluster-stack';
import { FargateServiceStack } from '../lib/fargate-service-stack';

const immutableImageTag =
  '0123456789abcdef0123456789abcdef01234567';

interface TestTemplates {
  ecr: Template;
  fargate: Template;
}

function createTemplates(): TestTemplates {
  const app = new cdk.App();

  const ecrStack = new EcrStack(
    app,
    'TestEcrStack',
  );

  const networkStack = new NetworkStack(
    app,
    'TestNetworkStack',
  );

  const clusterStack = new EcsClusterStack(
    app,
    'TestClusterStack',
    {
      vpc: networkStack.vpc,
    },
  );

  const fargateStack = new FargateServiceStack(
    app,
    'TestFargateStack',
    {
      vpc: networkStack.vpc,
      cluster: clusterStack.cluster,
      repository: ecrStack.repository,
      imageTag: immutableImageTag,
    },
  );

  return {
    ecr: Template.fromStack(ecrStack),
    fargate: Template.fromStack(fargateStack),
  };
}

describe('production-parity infrastructure', () => {
  const templates = createTemplates();

  test('ECR repository uses immutable image tags', () => {
    templates.ecr.hasResourceProperties(
      'AWS::ECR::Repository',
      {
        RepositoryName: 'demo-inference-api',
        ImageTagMutability: 'IMMUTABLE',
      },
    );
  });

  test('Application Load Balancer is internal', () => {
    templates.fargate.hasResourceProperties(
      'AWS::ElasticLoadBalancingV2::LoadBalancer',
      {
        Name: 'llm-parity-private-alb',
        Scheme: 'internal',
        Type: 'application',
      },
    );
  });

  test('Fargate service does not assign public IP addresses', () => {
    templates.fargate.hasResourceProperties(
      'AWS::ECS::Service',
      {
        ServiceName:
          'llm-parity-demo-inference-service',
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

  test('Fargate service enables deployment rollback', () => {
    templates.fargate.hasResourceProperties(
      'AWS::ECS::Service',
      {
        DeploymentConfiguration: Match.objectLike({
          DeploymentCircuitBreaker: {
            Enable: true,
            Rollback: true,
          },
        }),
      },
    );
  });

  test('task definition uses the requested immutable image', () => {
    const taskDefinitions =
      templates.fargate.findResources(
        'AWS::ECS::TaskDefinition',
      );

    expect(Object.keys(taskDefinitions)).toHaveLength(1);

    const taskDefinition = Object.values(
      taskDefinitions,
    )[0] as {
      Properties: {
        Cpu: string;
        Memory: string;
        NetworkMode: string;
        RequiresCompatibilities: string[];
        ContainerDefinitions: Array<{
          Name: string;
          Image: unknown;
          Environment: Array<{
            Name: string;
            Value: string;
          }>;
          PortMappings: Array<{
            ContainerPort: number;
            Protocol: string;
          }>;
        }>;
      };
    };

    expect(taskDefinition.Properties).toEqual(
      expect.objectContaining({
        Cpu: '256',
        Memory: '512',
        NetworkMode: 'awsvpc',
        RequiresCompatibilities: ['FARGATE'],
      }),
    );

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
        {
          Name: 'PORT',
          Value: '3000',
        },
      ]),
    );

    expect(container?.PortMappings).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ContainerPort: 3000,
          Protocol: 'tcp',
        }),
      ]),
    );
  });

  test('target group checks the health endpoint on port 3000', () => {
    templates.fargate.hasResourceProperties(
      'AWS::ElasticLoadBalancingV2::TargetGroup',
      {
        Port: 3000,
        Protocol: 'HTTP',
        TargetType: 'ip',
        HealthCheckEnabled: true,
        HealthCheckPath: '/health',
        Matcher: {
          HttpCode: '200',
        },
      },
    );
  });

  test('ALB access to the service is limited to port 3000', () => {
    templates.fargate.hasResourceProperties(
      'AWS::EC2::SecurityGroupIngress',
      {
        IpProtocol: 'tcp',
        FromPort: 3000,
        ToPort: 3000,
      },
    );
  });
});
