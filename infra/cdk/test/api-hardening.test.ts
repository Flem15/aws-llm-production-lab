import * as cdk from 'aws-cdk-lib';
import {
  Match,
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
  const app =
    new cdk.App();

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
      'HardeningEcrStack',
      commonProps,
    );

  const networkStack =
    new NetworkStack(
      app,
      'HardeningNetworkStack',
      commonProps,
    );

  const clusterStack =
    new EcsClusterStack(
      app,
      'HardeningClusterStack',
      {
        ...commonProps,
        vpc:
          networkStack.vpc,
      },
    );

  const fargateStack =
    new FargateServiceStack(
      app,
      'HardeningFargateStack',
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
      'HardeningApiStack',
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
  'HTTP API request hardening',
  () => {
    const template =
      createTemplate();

    test(
      'CORS uses only the approved development origins',
      () => {
        template.hasResourceProperties(
          'AWS::ApiGatewayV2::Api',
          {
            CorsConfiguration: {
              AllowOrigins: [
                'http://localhost:3000',
                'http://127.0.0.1:3000',
              ],
              AllowMethods: [
                'GET',
                'POST',
                'OPTIONS',
              ],
              AllowHeaders:
                Match.arrayWith([
                  'authorization',
                  'content-type',
                  'x-amz-content-sha256',
                  'x-amz-date',
                  'x-amz-security-token',
                  'x-request-id',
                ]),
              ExposeHeaders: [
                'x-request-id',
              ],
              MaxAge:
                3600,
            },
          },
        );
      },
    );

    test(
      'CORS does not allow wildcard origins',
      () => {
        const apis =
          template.findResources(
            'AWS::ApiGatewayV2::Api',
          );

        const serialized =
          JSON.stringify(
            apis,
          );

        expect(
          serialized,
        ).not.toContain(
          '"AllowOrigins":["*"]',
        );
      },
    );

    test(
      'POST /invoke still requires AWS IAM',
      () => {
        const routes =
          template.findResources(
            'AWS::ApiGatewayV2::Route',
          );

        const invokeRoutes =
          Object.values(
            routes,
          ).filter(
            (resource) =>
              resource
                .Properties
                ?.RouteKey ===
              'POST /invoke',
          );

        expect(
          invokeRoutes,
        ).toHaveLength(1);

        expect(
          invokeRoutes[0]
            .Properties
            ?.AuthorizationType,
        ).toBe(
          'AWS_IAM',
        );
      },
    );

    test(
      'default stage retains 10 rps and 20 burst throttling',
      () => {
        template.hasResourceProperties(
          'AWS::ApiGatewayV2::Stage',
          {
            DefaultRouteSettings: {
              ThrottlingRateLimit:
                10,
              ThrottlingBurstLimit:
                20,
            },
          },
        );
      },
    );
  },
);
