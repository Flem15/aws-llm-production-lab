import {
  CfnOutput,
  Stack,
  StackProps,
} from 'aws-cdk-lib';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';

export interface IncidentResponseStackProps
  extends StackProps {
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

    this.incidentTopic = new sns.Topic(
      this,
      'IncidentAlertsTopic',
      {
        topicName:
          'llm-parity-dev-incident-alerts',
        displayName:
          'LLM Parity Dev Incident Alerts',
      },
    );

    const alarmStateRule = new events.Rule(
      this,
      'OperationalAlarmStateRule',
      {
        ruleName:
          'llm-parity-operational-alarm-alert',
        description:
          'Routes LLM parity CloudWatch ALARM state changes to the incident topic',
        eventPattern: {
          source: ['aws.cloudwatch'],
          detailType: [
            'CloudWatch Alarm State Change',
          ],
          detail: {
            alarmName: [
              'llm-parity-ecs-high-cpu',
              'llm-parity-ecs-high-memory',
              'llm-parity-alb-unhealthy-target',
              'llm-parity-alb-5xx',
              'llm-parity-api-5xx',
              'llm-parity-api-high-latency',
              'llm-parity-api-4xx',
              'llm-parity-alb-high-target-response-time',
            ],
            state: {
              value: ['ALARM'],
            },
          },
        },
      },
    );

    alarmStateRule.addTarget(
      new targets.SnsTopic(
        this.incidentTopic,
      ),
    );

    const abnormalTaskStopRule =
      new events.Rule(
        this,
        'AbnormalTaskStopRule',
        {
          ruleName:
            'llm-parity-abnormal-task-stop',
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

    abnormalTaskStopRule.addTarget(
      new targets.SnsTopic(
        this.incidentTopic,
      ),
    );

    const deploymentFailureRule =
      new events.Rule(
        this,
        'DeploymentFailureRule',
        {
          ruleName:
            'llm-parity-deployment-failure',
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

    deploymentFailureRule.addTarget(
      new targets.SnsTopic(
        this.incidentTopic,
      ),
    );

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
