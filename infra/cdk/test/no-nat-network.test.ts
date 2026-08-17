import * as cdk from 'aws-cdk-lib';
import {
  Template,
} from 'aws-cdk-lib/assertions';
import {
  NetworkStack,
} from '../lib/network-stack';

function createTemplate(): Template {
  const app =
    new cdk.App();

  const stack =
    new NetworkStack(
      app,
      'NoNatNetworkTestStack',
      {
        env: {
          account:
            '111111111111',
          region:
            'us-east-2',
        },
      },
    );

  return Template.fromStack(
    stack,
  );
}

describe(
  'isolated private network',
  () => {
    const template =
      createTemplate();

    test(
      'creates no NAT Gateways',
      () => {
        template.resourceCountIs(
          'AWS::EC2::NatGateway',
          0,
        );
      },
    );

    test(
      'creates no NAT Elastic IP addresses',
      () => {
        template.resourceCountIs(
          'AWS::EC2::EIP',
          0,
        );
      },
    );

    test(
      'private route tables have no NAT Gateway routes',
      () => {
        const routes =
          template.findResources(
            'AWS::EC2::Route',
          );

        const natRoutes =
          Object.values(
            routes,
          ).filter(
            (resource) =>
              resource
                .Properties
                ?.NatGatewayId !==
              undefined,
          );

        expect(
          natRoutes,
        ).toHaveLength(
          0,
        );
      },
    );

    test(
      'VPC itself remains deployed',
      () => {
        template.resourceCountIs(
          'AWS::EC2::VPC',
          1,
        );
      },
    );
  },
);
