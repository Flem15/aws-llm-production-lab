import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as logs from 'aws-cdk-lib/aws-logs';

export interface FargateServiceStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
  cluster: ecs.ICluster;
  repository: ecr.IRepository;
}

export class FargateServiceStack extends cdk.Stack {
  public readonly taskDefinition: ecs.FargateTaskDefinition;
  public readonly service: ecs.FargateService;
  public readonly serviceSecurityGroup: ec2.SecurityGroup;

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
          'v1',
        ),
        environment: {
          APP_VERSION: 'v1',
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

    new cdk.CfnOutput(this, 'TaskDefinitionFamily', {
      value: this.taskDefinition.family,
    });

    new cdk.CfnOutput(this, 'ServiceName', {
      value: this.service.serviceName,
    });

    new cdk.CfnOutput(this, 'ServiceSecurityGroupId', {
      value: this.serviceSecurityGroup.securityGroupId,
    });

    new cdk.CfnOutput(this, 'LogGroupName', {
      value: logGroup.logGroupName,
    });
  }
}
