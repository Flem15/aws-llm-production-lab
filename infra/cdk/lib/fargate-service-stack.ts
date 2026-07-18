import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as logs from 'aws-cdk-lib/aws-logs';

export interface FargateServiceStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
  cluster: ecs.ICluster;
  repository: ecr.IRepository;
  imageTag: string;
}

export class FargateServiceStack extends cdk.Stack {
  public readonly taskDefinition: ecs.FargateTaskDefinition;
  public readonly service: ecs.FargateService;
  public readonly serviceSecurityGroup: ec2.SecurityGroup;
  public readonly loadBalancerSecurityGroup: ec2.SecurityGroup;
  public readonly loadBalancer: elbv2.ApplicationLoadBalancer;
  public readonly listener: elbv2.ApplicationListener;
  public readonly targetGroup: elbv2.ApplicationTargetGroup;

  constructor(
    scope: Construct,
    id: string,
    props: FargateServiceStackProps,
  ) {
    super(scope, id, props);

    const logGroup = new logs.LogGroup(this, 'DemoInferenceLogGroup', {
      logGroupName: '/ecs/llm-parity/demo-inference-api',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.taskDefinition = new ecs.FargateTaskDefinition(
      this,
      'DemoInferenceTaskDefinition',
      {
        family: 'llm-parity-demo-inference',
        cpu: 256,
        memoryLimitMiB: 512,
      },
    );

    const container = this.taskDefinition.addContainer(
      'DemoInferenceContainer',
      {
        containerName: 'demo-inference-api',
        image: ecs.ContainerImage.fromEcrRepository(
          props.repository,
          props.imageTag,
        ),
        environment: {
          APP_VERSION: props.imageTag,
          PORT: '3000',
        },
        logging: ecs.LogDrivers.awsLogs({
          logGroup,
          streamPrefix: 'demo-inference-api',
        }),
        healthCheck: {
          command: [
            'CMD-SHELL',
            'wget -qO- http://localhost:3000/health || exit 1',
          ],
          interval: cdk.Duration.seconds(30),
          timeout: cdk.Duration.seconds(5),
          retries: 3,
          startPeriod: cdk.Duration.seconds(20),
        },
      },
    );

    container.addPortMappings({
      name: 'api',
      containerPort: 3000,
      protocol: ecs.Protocol.TCP,
      appProtocol: ecs.AppProtocol.http,
    });

    this.serviceSecurityGroup = new ec2.SecurityGroup(
      this,
      'DemoInferenceServiceSecurityGroup',
      {
        vpc: props.vpc,
        securityGroupName: 'llm-parity-demo-inference-service-sg',
        description:
          'Security group for the demo inference ECS Fargate service',
        allowAllOutbound: true,
      },
    );

    this.loadBalancerSecurityGroup = new ec2.SecurityGroup(
      this,
      'PrivateAlbSecurityGroup',
      {
        vpc: props.vpc,
        securityGroupName: 'llm-parity-private-alb-sg',
        description:
          'Security group for the private demo inference application load balancer',
        allowAllOutbound: true,
      },
    );

    this.serviceSecurityGroup.addIngressRule(
      this.loadBalancerSecurityGroup,
      ec2.Port.tcp(3000),
      'Allow the private ALB to reach the inference container',
    );

    this.service = new ecs.FargateService(
      this,
      'DemoInferenceService',
      {
        serviceName: 'llm-parity-demo-inference-service',
        cluster: props.cluster,
        taskDefinition: this.taskDefinition,
        desiredCount: 1,
        assignPublicIp: false,
        securityGroups: [this.serviceSecurityGroup],
        vpcSubnets: {
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
        circuitBreaker: {
          rollback: true,
        },
        healthCheckGracePeriod: cdk.Duration.seconds(60),
        minHealthyPercent: 100,
        maxHealthyPercent: 200,
      },
    );

    this.loadBalancer = new elbv2.ApplicationLoadBalancer(
      this,
      'PrivateApplicationLoadBalancer',
      {
        vpc: props.vpc,
        loadBalancerName: 'llm-parity-private-alb',
        internetFacing: false,
        securityGroup: this.loadBalancerSecurityGroup,
        vpcSubnets: {
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
      },
    );

    this.listener = this.loadBalancer.addListener(
      'PrivateHttpListener',
      {
        port: 80,
        protocol: elbv2.ApplicationProtocol.HTTP,
        open: false,
      },
    );

    this.targetGroup = this.listener.addTargets(
      'DemoInferenceTargets',
      {
        port: 3000,
        protocol: elbv2.ApplicationProtocol.HTTP,
        targets: [this.service],
        deregistrationDelay: cdk.Duration.seconds(30),
        healthCheck: {
          enabled: true,
          path: '/health',
          protocol: elbv2.Protocol.HTTP,
          port: 'traffic-port',
          healthyHttpCodes: '200',
          healthyThresholdCount: 2,
          unhealthyThresholdCount: 3,
          interval: cdk.Duration.seconds(30),
          timeout: cdk.Duration.seconds(5),
        },
      },
    );

    new cdk.CfnOutput(this, 'TaskDefinitionFamily', {
      value: this.taskDefinition.family,
    });

    new cdk.CfnOutput(this, 'ServiceName', {
      value: this.service.serviceName,
    });

    new cdk.CfnOutput(this, 'ServiceSecurityGroupId', {
      value: this.serviceSecurityGroup.securityGroupId,
    });

    new cdk.CfnOutput(this, 'LoadBalancerSecurityGroupId', {
      value: this.loadBalancerSecurityGroup.securityGroupId,
    });

    new cdk.CfnOutput(this, 'PrivateLoadBalancerDnsName', {
      value: this.loadBalancer.loadBalancerDnsName,
    });

    new cdk.CfnOutput(this, 'TargetGroupArn', {
      value: this.targetGroup.targetGroupArn,
    });

    new cdk.CfnOutput(this, 'LogGroupName', {
      value: logGroup.logGroupName,
    });
  }
}
