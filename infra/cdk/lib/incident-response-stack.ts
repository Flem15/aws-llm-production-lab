import {
  CfnOutput,
  RemovalPolicy,
  Stack,
  StackProps,
} from 'aws-cdk-lib';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface IncidentResponseStackProps
  extends StackProps {
  readonly environment?: 'dev' | 'prod';
  readonly cluster: ecs.ICluster;
}

export class IncidentResponseStack extends Stack {
  public readonly incidentTopic: sns.Topic;

  constructor(
    scope: Construct,
    id: string,
    props: IncidentResponseStackProps,
  ) {
    super(scope, id, props);

    // Development remediation only; production changes require a separate review.
    const notificationKey = props.environment !== 'prod' ? new kms.Key(this, 'IncidentNotificationKey', {
      enableKeyRotation: true,
      removalPolicy: RemovalPolicy.RETAIN,
      description: 'Development incident notification encryption',
    }) : undefined;

    this.incidentTopic = new sns.Topic(
      this,
      'IncidentAlertsTopic',
      {
        masterKey: notificationKey,
        topicName:
          (props.environment === 'prod'
          ? 'llm-parity-prod-incident-alerts'
          : 'llm-parity-dev-incident-alerts'),
        displayName:
          (props.environment === 'prod'
          ? 'LLM Parity Prod Incident Alerts'
          : 'LLM Parity Dev Incident Alerts'),
      },
    );

    const alarmStateRule = new events.Rule(
      this,
      'OperationalAlarmStateRule',
      {
        ruleName:
          (props.environment === 'prod'
          ? 'llm-parity-prod-operational-alarm-alert'
          : 'llm-parity-operational-alarm-alert'),
        description:
          'Routes LLM parity CloudWatch ALARM state changes to the incident topic',
        eventPattern: {
          source: ['aws.cloudwatch'],
          detailType: [
            'CloudWatch Alarm State Change',
          ],
          detail: {
            alarmName: [
              (props.environment === 'prod'
          ? 'llm-parity-prod-ecs-high-cpu'
          : 'llm-parity-ecs-high-cpu'),
              (props.environment === 'prod'
          ? 'llm-parity-prod-ecs-high-memory'
          : 'llm-parity-ecs-high-memory'),
              (props.environment === 'prod'
          ? 'llm-parity-prod-alb-unhealthy-target'
          : 'llm-parity-alb-unhealthy-target'),
              (props.environment === 'prod'
          ? 'llm-parity-prod-alb-5xx'
          : 'llm-parity-alb-5xx'),
              (props.environment === 'prod'
          ? 'llm-parity-prod-api-5xx'
          : 'llm-parity-api-5xx'),
              (props.environment === 'prod'
          ? 'llm-parity-prod-api-high-latency'
          : 'llm-parity-api-high-latency'),
              (props.environment === 'prod'
          ? 'llm-parity-prod-api-4xx'
          : 'llm-parity-api-4xx'),
              (props.environment === 'prod'
          ? 'llm-parity-prod-alb-high-target-response-time'
          : 'llm-parity-alb-high-target-response-time'),
            ],
            state: {
              value: ['ALARM'],
            },
          },
        },
      },
    );

    if (notificationKey) {
      const publisher = new iam.Role(this, 'alarmStateRulePublisher', {
        assumedBy: new iam.ServicePrincipal('events.amazonaws.com', { conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnEquals: { 'aws:SourceArn': `arn:${this.partition}:events:${this.region}:${this.account}:rule/llm-parity-operational-alarm-alert` },
        } }),
      });
      alarmStateRule.addTarget(new targets.SnsTopic(this.incidentTopic, { role: publisher }));
    } else {
      alarmStateRule.addTarget(new targets.SnsTopic(this.incidentTopic));
    }

    const abnormalTaskStopRule =
      new events.Rule(
        this,
        'AbnormalTaskStopRule',
        {
          ruleName:
            (props.environment === 'prod'
          ? 'llm-parity-prod-abnormal-task-stop'
          : 'llm-parity-abnormal-task-stop'),
          description:
            'Alerts on abnormal ECS task stops while excluding normal service scheduler stops',
          eventPattern: {
            source: ['aws.ecs'],
            detailType: [
              'ECS Task State Change',
            ],
            detail: {
              clusterArn: [
                props.cluster.clusterArn,
              ],
              lastStatus: ['STOPPED'],
              stopCode: [
                {
                  'anything-but':
                    'ServiceSchedulerInitiated',
                },
              ],
            },
          },
        },
      );

    if (notificationKey) {
      const publisher = new iam.Role(this, 'abnormalTaskStopRulePublisher', {
        assumedBy: new iam.ServicePrincipal('events.amazonaws.com', { conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnEquals: { 'aws:SourceArn': `arn:${this.partition}:events:${this.region}:${this.account}:rule/llm-parity-abnormal-task-stop` },
        } }),
      });
      abnormalTaskStopRule.addTarget(new targets.SnsTopic(this.incidentTopic, { role: publisher }));
    } else {
      abnormalTaskStopRule.addTarget(new targets.SnsTopic(this.incidentTopic));
    }

    const deploymentFailureRule =
      new events.Rule(
        this,
        'DeploymentFailureRule',
        {
          ruleName:
            (props.environment === 'prod'
          ? 'llm-parity-prod-deployment-failure'
          : 'llm-parity-deployment-failure'),
          description:
            'Alerts when an ECS service deployment fails',
          eventPattern: {
            source: ['aws.ecs'],
            detailType: [
              'ECS Deployment State Change',
            ],
            detail: {
              clusterArn: [
                props.cluster.clusterArn,
              ],
              eventName: [
                'SERVICE_DEPLOYMENT_FAILED',
              ],
            },
          },
        },
      );

    if (notificationKey) {
      const publisher = new iam.Role(this, 'deploymentFailureRulePublisher', {
        assumedBy: new iam.ServicePrincipal('events.amazonaws.com', { conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnEquals: { 'aws:SourceArn': `arn:${this.partition}:events:${this.region}:${this.account}:rule/llm-parity-deployment-failure` },
        } }),
      });
      deploymentFailureRule.addTarget(new targets.SnsTopic(this.incidentTopic, { role: publisher }));
    } else {
      deploymentFailureRule.addTarget(new targets.SnsTopic(this.incidentTopic));
    }

    new CfnOutput(
      this,
      'IncidentAlertsTopicArn',
      {
        description:
          'SNS topic receiving operational incident notifications',
        value:
          this.incidentTopic.topicArn,
      },
    );

    new CfnOutput(
      this,
      'OperationalAlarmRuleName',
      {
        value:
          alarmStateRule.ruleName,
      },
    );

    new CfnOutput(
      this,
      'AbnormalTaskStopRuleName',
      {
        value:
          abnormalTaskStopRule.ruleName,
      },
    );

    new CfnOutput(
      this,
      'DeploymentFailureRuleName',
      {
        value:
          deploymentFailureRule.ruleName,
      },
    );
  }
}
