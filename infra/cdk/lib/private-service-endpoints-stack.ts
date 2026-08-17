import {
  CfnOutput,
  Stack,
  StackProps,
} from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';

export interface PrivateServiceEndpointsStackProps
  extends StackProps {
  readonly vpc: ec2.IVpc;

  readonly serviceSecurityGroup:
    ec2.ISecurityGroup;
}

export class PrivateServiceEndpointsStack
  extends Stack {
  public readonly endpointSecurityGroup:
    ec2.SecurityGroup;

  constructor(
    scope: Construct,
    id: string,
    props:
      PrivateServiceEndpointsStackProps,
  ) {
    super(
      scope,
      id,
      props,
    );

    this.endpointSecurityGroup =
      new ec2.SecurityGroup(
        this,
        'EndpointSecurityGroup',
        {
          vpc:
            props.vpc,
          description:
            'Allows private ECS tasks to reach AWS service interface endpoints over TLS',
          allowAllOutbound:
            false,
        },
      );

    this.endpointSecurityGroup
      .addIngressRule(
        props.serviceSecurityGroup,
        ec2.Port.tcp(443),
        'Allow ECS service tasks to reach private AWS service endpoints',
      );

    const privateSubnetSelection = {
      subnetType:
        ec2.SubnetType
          .PRIVATE_WITH_EGRESS,
      onePerAz:
        true,
    };

    const ecrApiEndpoint =
      new ec2.InterfaceVpcEndpoint(
        this,
        'EcrApiEndpoint',
        {
          vpc:
            props.vpc,
          service:
            ec2.InterfaceVpcEndpointAwsService
              .ECR,
          privateDnsEnabled:
            true,
          subnets:
            privateSubnetSelection,
          securityGroups: [
            this.endpointSecurityGroup,
          ],
        },
      );

    const ecrDockerEndpoint =
      new ec2.InterfaceVpcEndpoint(
        this,
        'EcrDockerEndpoint',
        {
          vpc:
            props.vpc,
          service:
            ec2.InterfaceVpcEndpointAwsService
              .ECR_DOCKER,
          privateDnsEnabled:
            true,
          subnets:
            privateSubnetSelection,
          securityGroups: [
            this.endpointSecurityGroup,
          ],
        },
      );

    const logsEndpoint =
      new ec2.InterfaceVpcEndpoint(
        this,
        'CloudWatchLogsEndpoint',
        {
          vpc:
            props.vpc,
          service:
            ec2.InterfaceVpcEndpointAwsService
              .CLOUDWATCH_LOGS,
          privateDnsEnabled:
            true,
          subnets:
            privateSubnetSelection,
          securityGroups: [
            this.endpointSecurityGroup,
          ],
        },
      );

    const s3Endpoint =
      new ec2.GatewayVpcEndpoint(
        this,
        'S3GatewayEndpoint',
        {
          vpc:
            props.vpc,
          service:
            ec2.GatewayVpcEndpointAwsService
              .S3,
          subnets: [
            privateSubnetSelection,
          ],
        },
      );

    new CfnOutput(
      this,
      'EcrApiEndpointId',
      {
        value:
          ecrApiEndpoint
            .vpcEndpointId,
      },
    );

    new CfnOutput(
      this,
      'EcrDockerEndpointId',
      {
        value:
          ecrDockerEndpoint
            .vpcEndpointId,
      },
    );

    new CfnOutput(
      this,
      'CloudWatchLogsEndpointId',
      {
        value:
          logsEndpoint
            .vpcEndpointId,
      },
    );

    new CfnOutput(
      this,
      'S3GatewayEndpointId',
      {
        value:
          s3Endpoint
            .vpcEndpointId,
      },
    );
  }
}
