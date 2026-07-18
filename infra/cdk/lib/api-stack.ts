import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as logs from 'aws-cdk-lib/aws-logs';

export interface ApiStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
  listener: elbv2.IApplicationListener;
  loadBalancerSecurityGroup: ec2.ISecurityGroup;
}

export class ApiStack extends cdk.Stack {
  public readonly httpApi: apigwv2.HttpApi;
  public readonly vpcLink: apigwv2.VpcLink;
  public readonly vpcLinkSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    this.vpcLinkSecurityGroup = new ec2.SecurityGroup(
      this,
      'VpcLinkSecurityGroup',
      {
        vpc: props.vpc,
        securityGroupName: 'llm-parity-api-vpc-link-sg',
        description:
          'Security group used by API Gateway VPC Link',
        allowAllOutbound: true,
      },
    );

    new ec2.CfnSecurityGroupIngress(
      this,
      'AllowVpcLinkToPrivateAlb',
      {
        groupId:
          props.loadBalancerSecurityGroup.securityGroupId,
        sourceSecurityGroupId:
          this.vpcLinkSecurityGroup.securityGroupId,
        ipProtocol: 'tcp',
        fromPort: 80,
        toPort: 80,
        description:
          'Allow API Gateway VPC Link to reach the private ALB listener',
      },
    );

    this.vpcLink = new apigwv2.VpcLink(
      this,
      'PrivateAlbVpcLink',
      {
        vpc: props.vpc,
        vpcLinkName: 'llm-parity-private-alb-vpc-link',
        securityGroups: [this.vpcLinkSecurityGroup],
        subnets: {
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
      },
    );

    const accessLogGroup = new logs.LogGroup(
      this,
      'HttpApiAccessLogGroup',
      {
        logGroupName: '/aws/apigateway/llm-parity-http-api',
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      },
    );

    this.httpApi = new apigwv2.HttpApi(
      this,
      'LlmParityHttpApi',
      {
        apiName: 'llm-parity-http-api',
        description:
          'Public HTTP API with a private ALB integration',
        createDefaultStage: true,
      },
    );

    const defaultStage =
      this.httpApi.defaultStage?.node.defaultChild as
        apigwv2.CfnStage;

    defaultStage.defaultRouteSettings = {
      throttlingBurstLimit: 20,
      throttlingRateLimit: 10,
    };

    defaultStage.accessLogSettings = {
      destinationArn: accessLogGroup.logGroupArn,
      format: JSON.stringify({
        requestId: '$context.requestId',
        requestTime: '$context.requestTime',
        httpMethod: '$context.httpMethod',
        routeKey: '$context.routeKey',
        status: '$context.status',
        responseLength: '$context.responseLength',
        integrationError:
          '$context.integrationErrorMessage',
      }),
    };

    const integration = new integrations.HttpAlbIntegration(
      'PrivateAlbIntegration',
      props.listener,
      {
        vpcLink: this.vpcLink,
        parameterMapping: new apigwv2.ParameterMapping()
          .overwritePath(
            apigwv2.MappingValue.requestPath(),
          ),
      },
    );

    this.httpApi.addRoutes({
      path: '/health',
      methods: [apigwv2.HttpMethod.GET],
      integration,
    });

    this.httpApi.addRoutes({
      path: '/invoke',
      methods: [apigwv2.HttpMethod.POST],
      integration,
    });

    new cdk.CfnOutput(this, 'HttpApiId', {
      value: this.httpApi.httpApiId,
    });

    new cdk.CfnOutput(this, 'HttpApiEndpoint', {
      value: this.httpApi.apiEndpoint,
    });

    new cdk.CfnOutput(this, 'VpcLinkId', {
      value: this.vpcLink.vpcLinkId,
    });

    new cdk.CfnOutput(this, 'ApiAccessLogGroupName', {
      value: accessLogGroup.logGroupName,
    });
  }
}
