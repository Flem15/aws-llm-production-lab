import * as cdk from 'aws-cdk-lib';
import {
  Template,
} from 'aws-cdk-lib/assertions';
import { EcrStack } from '../lib/ecr-stack';
import { NetworkStack } from '../lib/network-stack';
import {
  EcsClusterStack,
} from '../lib/ecs-cluster-stack';
import {
  FargateServiceStack,
} from '../lib/fargate-service-stack';
import { ApiStack } from '../lib/api-stack';

const immutableImageTag =
  '0123456789abcdef0123456789abcdef01234567';

function createTemplate(): Template {
  const app = new cdk.App();

  const commonProps:
    cdk.StackProps = {
      env: {
        account:
          '111111111111',
        region:
          'us-east-2',
      },
    };

  const ecrStack =
    new EcrStack(
      app,
      'CorrelationEcrStack',
      commonProps,
    );

  const networkStack =
    new NetworkStack(
      app,
      'CorrelationNetworkStack',
      commonProps,
    );

  const clusterStack =
    new EcsClusterStack(
      app,
      'CorrelationClusterStack',
      {
        ...commonProps,
        vpc:
          networkStack.vpc,
      },
    );

  const fargateStack =
    new FargateServiceStack(
      app,
      'CorrelationFargateStack',
      {
        ...commonProps,
        vpc:
          networkStack.vpc,
        cluster:
          clusterStack.cluster,
        repository:
          ecrStack.repository,
        imageTag:
          immutableImageTag,
      },
    );

  const apiStack =
    new ApiStack(
      app,
      'CorrelationApiStack',
      {
        ...commonProps,
        vpc:
          networkStack.vpc,
        listener:
          fargateStack.listener,
        loadBalancerSecurityGroup:
          fargateStack
            .loadBalancerSecurityGroup,
      },
    );

  return Template.fromStack(
    apiStack,
  );
}

describe(
  'request correlation',
  () => {
    const template =
      createTemplate();

    test(
      'API access logs include requestId',
      () => {
        const stages =
          template.findResources(
            'AWS::ApiGatewayV2::Stage',
          );

        const serialized =
          JSON.stringify(stages);

        expect(
          serialized,
        ).toContain(
          '$context.requestId',
        );
      },
    );

    test(
      'API Gateway propagates requestId to the private backend',
      () => {
        const integrations =
          template.findResources(
            'AWS::ApiGatewayV2::Integration',
          );

        const serialized =
          JSON.stringify(
            integrations,
          );

        expect(
          serialized,
        ).toContain(
          'x-request-id',
        );

        expect(
          serialized,
        ).toContain(
          '$context.requestId',
        );
      },
    );
  },
);
