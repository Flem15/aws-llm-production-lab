import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';

export interface ObservabilityStackProps extends cdk.StackProps {
  service: ecs.FargateService;
  loadBalancer: elbv2.IApplicationLoadBalancer;
  targetGroup: elbv2.IApplicationTargetGroup;
  httpApi: apigwv2.IHttpApi;
}

export class ObservabilityStack extends cdk.Stack {
  public readonly dashboard: cloudwatch.Dashboard;

  constructor(
    scope: Construct,
    id: string,
    props: ObservabilityStackProps,
  ) {
    super(scope, id, props);

    const oneMinute = cdk.Duration.minutes(1);

    const cpuMetric = props.service.metricCpuUtilization({
      period: oneMinute,
      statistic: cloudwatch.Stats.AVERAGE,
      label: 'ECS CPU utilization',
    });

    const memoryMetric = props.service.metricMemoryUtilization({
      period: oneMinute,
      statistic: cloudwatch.Stats.AVERAGE,
      label: 'ECS memory utilization',
    });

    const unhealthyHostMetric =
      props.targetGroup.metrics.unhealthyHostCount({
        period: oneMinute,
        statistic: cloudwatch.Stats.MAXIMUM,
        label: 'Unhealthy ALB targets',
      });

    const alb5xxMetric =
      props.loadBalancer.metrics.httpCodeElb(
        elbv2.HttpCodeElb.ELB_5XX_COUNT,
        {
          period: oneMinute,
          statistic: cloudwatch.Stats.SUM,
          label: 'ALB-generated 5XX responses',
        },
      );

    const api5xxMetric = props.httpApi.metricServerError({
      period: oneMinute,
      statistic: cloudwatch.Stats.SUM,
      label: 'API Gateway 5XX responses',
    });

    const apiLatencyMetric = props.httpApi.metricLatency({
      period: oneMinute,
      statistic: 'p95',
      label: 'API Gateway p95 latency',
    });

    const apiCountMetric = props.httpApi.metricCount({
      period: oneMinute,
      statistic: cloudwatch.Stats.SUM,
      label: 'API Gateway request count',
    });

    const cpuAlarm = new cloudwatch.Alarm(
      this,
      'HighCpuAlarm',
      {
        alarmName: 'llm-parity-ecs-high-cpu',
        alarmDescription:
          'ECS service CPU utilization is at or above 80 percent.',
        metric: cpuMetric,
        threshold: 80,
        evaluationPeriods: 3,
        datapointsToAlarm: 2,
        comparisonOperator:
          cloudwatch.ComparisonOperator
            .GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData:
          cloudwatch.TreatMissingData.NOT_BREACHING,
      },
    );

    const memoryAlarm = new cloudwatch.Alarm(
      this,
      'HighMemoryAlarm',
      {
        alarmName: 'llm-parity-ecs-high-memory',
        alarmDescription:
          'ECS service memory utilization is at or above 80 percent.',
        metric: memoryMetric,
        threshold: 80,
        evaluationPeriods: 3,
        datapointsToAlarm: 2,
        comparisonOperator:
          cloudwatch.ComparisonOperator
            .GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData:
          cloudwatch.TreatMissingData.NOT_BREACHING,
      },
    );

    const unhealthyHostAlarm = new cloudwatch.Alarm(
      this,
      'UnhealthyHostAlarm',
      {
        alarmName: 'llm-parity-alb-unhealthy-target',
        alarmDescription:
          'The private ALB target group has an unhealthy target.',
        metric: unhealthyHostMetric,
        threshold: 1,
        evaluationPeriods: 2,
        datapointsToAlarm: 2,
        comparisonOperator:
          cloudwatch.ComparisonOperator
            .GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData:
          cloudwatch.TreatMissingData.NOT_BREACHING,
      },
    );

    const alb5xxAlarm = new cloudwatch.Alarm(
      this,
      'Alb5xxAlarm',
      {
        alarmName: 'llm-parity-alb-5xx',
        alarmDescription:
          'The private ALB generated five or more 5XX responses.',
        metric: alb5xxMetric,
        threshold: 5,
        evaluationPeriods: 1,
        datapointsToAlarm: 1,
        comparisonOperator:
          cloudwatch.ComparisonOperator
            .GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData:
          cloudwatch.TreatMissingData.NOT_BREACHING,
      },
    );

    const api5xxAlarm = new cloudwatch.Alarm(
      this,
      'Api5xxAlarm',
      {
        alarmName: 'llm-parity-api-5xx',
        alarmDescription:
          'API Gateway returned five or more 5XX responses.',
        metric: api5xxMetric,
        threshold: 5,
        evaluationPeriods: 1,
        datapointsToAlarm: 1,
        comparisonOperator:
          cloudwatch.ComparisonOperator
            .GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData:
          cloudwatch.TreatMissingData.NOT_BREACHING,
      },
    );

    const apiLatencyAlarm = new cloudwatch.Alarm(
      this,
      'ApiLatencyAlarm',
      {
        alarmName: 'llm-parity-api-high-latency',
        alarmDescription:
          'API Gateway p95 latency is at or above 2000 milliseconds.',
        metric: apiLatencyMetric,
        threshold: 2000,
        evaluationPeriods: 3,
        datapointsToAlarm: 2,
        comparisonOperator:
          cloudwatch.ComparisonOperator
            .GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData:
          cloudwatch.TreatMissingData.NOT_BREACHING,
      },
    );

    this.dashboard = new cloudwatch.Dashboard(
      this,
      'OperationsDashboard',
      {
        dashboardName: 'llm-parity-operations',
      },
    );

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        width: 24,
        height: 2,
        markdown:
          '# AWS LLM Production Parity Lab\n' +
          'ECS, private ALB, and HTTP API operational metrics.',
      }),
    );

    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'ECS utilization',
        width: 12,
        height: 6,
        left: [cpuMetric, memoryMetric],
        leftYAxis: {
          min: 0,
          max: 100,
          label: 'Percent',
          showUnits: false,
        },
      }),
      new cloudwatch.GraphWidget({
        title: 'ALB target health and 5XX',
        width: 12,
        height: 6,
        left: [unhealthyHostMetric, alb5xxMetric],
      }),
    );

    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'API traffic and server errors',
        width: 12,
        height: 6,
        left: [apiCountMetric, api5xxMetric],
      }),
      new cloudwatch.GraphWidget({
        title: 'API Gateway p95 latency',
        width: 12,
        height: 6,
        left: [apiLatencyMetric],
        leftYAxis: {
          min: 0,
          label: 'Milliseconds',
          showUnits: false,
        },
      }),
    );

    this.dashboard.addWidgets(
      new cloudwatch.AlarmStatusWidget({
        title: 'Operational alarm status',
        width: 24,
        height: 6,
        alarms: [
          cpuAlarm,
          memoryAlarm,
          unhealthyHostAlarm,
          alb5xxAlarm,
          api5xxAlarm,
          apiLatencyAlarm,
        ],
      }),
    );

    new cdk.CfnOutput(this, 'DashboardName', {
      value: this.dashboard.dashboardName,
    });

    new cdk.CfnOutput(this, 'CpuAlarmName', {
      value: cpuAlarm.alarmName,
    });

    new cdk.CfnOutput(this, 'MemoryAlarmName', {
      value: memoryAlarm.alarmName,
    });

    new cdk.CfnOutput(this, 'UnhealthyHostAlarmName', {
      value: unhealthyHostAlarm.alarmName,
    });

    new cdk.CfnOutput(this, 'Alb5xxAlarmName', {
      value: alb5xxAlarm.alarmName,
    });

    new cdk.CfnOutput(this, 'Api5xxAlarmName', {
      value: api5xxAlarm.alarmName,
    });

    new cdk.CfnOutput(this, 'ApiLatencyAlarmName', {
      value: apiLatencyAlarm.alarmName,
    });
  }
}
