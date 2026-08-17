import * as cdk from 'aws-cdk-lib';
import {
  Match,
  Template,
} from 'aws-cdk-lib/assertions';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import {
  PrivateServiceEndpointsStack,
} from '../lib/private-service-endpoints-stack';

function createTemplate(): Template {
  const app =
    new cdk.App();

  const dependencies =
    new cdk.Stack(
      app,
      'EndpointDependencies',
      {
        env: {
          account:
            '111111111111',
          region:
            'us-east-2',
        },
      },
    );

  const vpc =
    new ec2.Vpc(
      dependencies,
      'Vpc',
      {
        maxAzs: 2,
        natGateways: 1,
      },
    );

  const serviceSecurityGroup =
    new ec2.SecurityGroup(
      dependencies,
      'ServiceSecurityGroup',
      {
        vpc,
      },
    );

  const stack =
    new PrivateServiceEndpointsStack(
      app,
      'PrivateEndpointsTestStack',
      {
        env: {
          account:
            '111111111111',
          region:
            'us-east-2',
        },
        vpc,
        serviceSecurityGroup,
      },
    );

  return Template.fromStack(
    stack,
  );
}

describe(
  'private AWS service endpoints',
  () => {
    const template =
      createTemplate();

    test(
      'creates exactly three interface endpoints',
      () => {
        template.resourceCountIs(
          'AWS::EC2::VPCEndpoint',
          4,
        );

        const endpoints =
          template.findResources(
            'AWS::EC2::VPCEndpoint',
          );

        const interfaceEndpoints =
          Object.values(
            endpoints,
          ).filter(
            (resource) =>
              resource
                .Properties
                ?.VpcEndpointType ===
              'Interface',
          );

        expect(
          interfaceEndpoints,
        ).toHaveLength(3);
      },
    );

    test(
      'creates an S3 gateway endpoint',
      () => {
        const endpoints =
          template.findResources(
            'AWS::EC2::VPCEndpoint',
          );

        const s3Endpoint =
          Object.values(
            endpoints,
          ).find(
            (resource) => {
              const properties =
                resource.Properties;

              if (
                properties
                  ?.VpcEndpointType !==
                'Gateway'
              ) {
                return false;
              }

              return JSON.stringify(
                properties.ServiceName,
              ).includes(
                '.s3',
              );
            },
          );

        expect(
          s3Endpoint,
        ).toBeDefined();

        expect(
          s3Endpoint
            ?.Properties
            ?.VpcEndpointType,
        ).toBe(
          'Gateway',
        );

        expect(
          s3Endpoint
            ?.Properties
            ?.RouteTableIds,
        ).toHaveLength(
          2,
        );

        expect(
          JSON.stringify(
            s3Endpoint
              ?.Properties
              ?.ServiceName,
          ),
        ).toContain(
          '.s3',
        );
      },
    );

    test(
      'creates private ECR API endpoint',
      () => {
        template.hasResourceProperties(
          'AWS::EC2::VPCEndpoint',
          {
            VpcEndpointType:
              'Interface',
            ServiceName:
              Match.stringLikeRegexp(
                'ecr\\.api$',
              ),
            PrivateDnsEnabled:
              true,
          },
        );
      },
    );

    test(
      'creates private ECR Docker endpoint',
      () => {
        template.hasResourceProperties(
          'AWS::EC2::VPCEndpoint',
          {
            VpcEndpointType:
              'Interface',
            ServiceName:
              Match.stringLikeRegexp(
                'ecr\\.dkr$',
              ),
            PrivateDnsEnabled:
              true,
          },
        );
      },
    );

    test(
      'creates private CloudWatch Logs endpoint',
      () => {
        template.hasResourceProperties(
          'AWS::EC2::VPCEndpoint',
          {
            VpcEndpointType:
              'Interface',
            ServiceName:
              Match.stringLikeRegexp(
                'logs$',
              ),
            PrivateDnsEnabled:
              true,
          },
        );
      },
    );

    test(
      'endpoint security group allows TLS from the ECS service security group',
      () => {
        template.hasResourceProperties(
          'AWS::EC2::SecurityGroupIngress',
          {
            IpProtocol:
              'tcp',
            FromPort:
              443,
            ToPort:
              443,
            SourceSecurityGroupId:
              Match.anyValue(),
            GroupId:
              Match.anyValue(),
          },
        );
      },
    );

    test(
      'endpoint security group does not allow arbitrary inbound CIDRs',
      () => {
        const ingress =
          template.findResources(
            'AWS::EC2::SecurityGroupIngress',
          );

        const serialized =
          JSON.stringify(
            ingress,
          );

        expect(
          serialized,
        ).not.toContain(
          '0.0.0.0/0',
        );
      },
    );
  },
);
