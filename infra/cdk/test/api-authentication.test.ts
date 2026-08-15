import * as cdk from 'aws-cdk-lib';
import {
  Template,
} from 'aws-cdk-lib/assertions';
import { EcrStack } from '../lib/ecr-stack';
import { NetworkStack } from '../lib/network-stack';
import { EcsClusterStack } from '../lib/ecs-cluster-stack';
import {
  FargateServiceStack,
} from '../lib/fargate-service-stack';
import { ApiStack } from '../lib/api-stack';

const immutableImageTag =
  '0123456789abcdef0123456789abcdef01234567';

function createApiTemplate(): Template {
  const app = new cdk.App();

  const commonProps: cdk.StackProps = {
    env: {
      account: '111111111111',
      region: 'us-east-2',
    },
  };

  const ecrStack = new EcrStack(
    app,
    'ApiAuthTestEcrStack',
    commonProps,
  );

  const networkStack = new NetworkStack(
    app,
    'ApiAuthTestNetworkStack',
    commonProps,
  );

  const clusterStack = new EcsClusterStack(
    app,
    'ApiAuthTestClusterStack',
    {
      ...commonProps,
      vpc: networkStack.vpc,
    },
  );

  const fargateStack =
    new FargateServiceStack(
      app,
      'ApiAuthTestFargateStack',
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
    'ApiAuthTestApiStack',
    {
      ...commonProps,
      vpc: networkStack.vpc,
      listener: fargateStack.listener,
      loadBalancerSecurityGroup:
        fargateStack.loadBalancerSecurityGroup,
    },
  );

  return Template.fromStack(apiStack);
}

function findRouteByKey(
  template: Template,
  routeKey: string,
): Record<string, unknown> {
  const routes = template.findResources(
    'AWS::ApiGatewayV2::Route',
  );

  const matchingRoutes =
    Object.values(routes).filter(
      (resource) =>
        resource.Properties?.RouteKey ===
        routeKey,
    );

  if (matchingRoutes.length !== 1) {
    throw new Error(
      `Expected exactly one ${routeKey} route, `
      + `found ${matchingRoutes.length}`,
    );
  }

  return matchingRoutes[0];
}

describe(
  'HTTP API authentication',
  () => {
    const template =
      createApiTemplate();

    test(
      'POST /invoke requires AWS IAM authorization',
      () => {
        const invokeRoute =
          findRouteByKey(
            template,
            'POST /invoke',
          );

        const invokeProperties =
          invokeRoute.Properties as {
            AuthorizationType?: string;
          };

        expect(
          invokeProperties.AuthorizationType,
        ).toBe('AWS_IAM');
      },
    );

    test(
      'GET /health remains unauthenticated',
      () => {
        const healthRoute =
          findRouteByKey(
            template,
            'GET /health',
          );

        const healthProperties =
          healthRoute.Properties as {
            AuthorizationType?: string;
          };

        const authorizationType =
          healthProperties.AuthorizationType;

        expect(
          authorizationType,
        ).not.toBe('AWS_IAM');

        expect(
          ['NONE', undefined],
        ).toContain(
          authorizationType,
        );
      },
    );

    test(
      'only one application route requires IAM',
      () => {
        const routes =
          template.findResources(
            'AWS::ApiGatewayV2::Route',
          );

        const iamRoutes =
          Object.values(routes).filter(
            (resource) =>
              resource.Properties
                ?.AuthorizationType ===
              'AWS_IAM',
          );

        expect(iamRoutes).toHaveLength(1);

        expect(
          iamRoutes[0].Properties
            ?.RouteKey,
        ).toBe('POST /invoke');
      },
    );

    test(
      'HTTP API contains no catch-all application route',
      () => {
        const routes =
          template.findResources(
            'AWS::ApiGatewayV2::Route',
          );

        const routeKeys =
          Object.values(routes).map(
            (resource) =>
              resource.Properties
                ?.RouteKey,
          );

        expect(
          routeKeys,
        ).not.toContain('$default');

        expect(
          routeKeys,
        ).not.toContain('ANY /{proxy+}');
      },
    );
  },
);
