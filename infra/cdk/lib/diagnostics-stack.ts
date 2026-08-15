import {
  Duration,
  Stack,
  StackProps,
} from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import { Construct } from 'constructs';

export interface DiagnosticsStackProps
  extends StackProps {
  readonly httpApi:
    apigwv2.HttpApi;

  readonly loadBalancer:
    elbv2.IApplicationLoadBalancer;
}

export class DiagnosticsStack
  extends Stack {
  constructor(
    scope: Construct,
    id: string,
    props: DiagnosticsStackProps,
  ) {
    super(
      scope,
      id,
      props,
    );

    const api4xxMetric =
      new cloudwatch.Metric({
        namespace:
          'AWS/ApiGateway',
        metricName:
          '4xx',
        dimensionsMap: {
          ApiId:
            props.httpApi.httpApiId,
          Stage:
            '$default',
        },
        period:
          Duration.minutes(5),
        statistic:
          'Sum',
        label:
          'API 4xx',
      });

    new cloudwatch.Alarm(
      this,
      'Api4xxAlarm',
      {
        alarmName:
          'llm-parity-api-4xx',
        alarmDescription:
          'Detects elevated client-side or authorization errors on the public HTTP API',
        metric:
          api4xxMetric,
        threshold:
          10,
        evaluationPeriods:
          1,
        datapointsToAlarm:
          1,
        comparisonOperator:
          cloudwatch
            .ComparisonOperator
            .GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData:
          cloudwatch
            .TreatMissingData
            .NOT_BREACHING,
      },
    );

    const targetResponseTime =
      props.loadBalancer
        .metrics
        .targetResponseTime({
          period:
            Duration.minutes(5),
          statistic:
            'p95',
          label:
            'ALB target response time p95',
        });

    new cloudwatch.Alarm(
      this,
      'AlbTargetResponseTimeAlarm',
      {
        alarmName:
          'llm-parity-alb-high-target-response-time',
        alarmDescription:
          'Detects sustained high p95 response time from private ECS targets',
        metric:
          targetResponseTime,
        threshold:
          2,
        evaluationPeriods:
          2,
        datapointsToAlarm:
          2,
        comparisonOperator:
          cloudwatch
            .ComparisonOperator
            .GREATER_THAN_THRESHOLD,
        treatMissingData:
          cloudwatch
            .TreatMissingData
            .NOT_BREACHING,
      },
    );

    const dashboard =
      new cloudwatch.Dashboard(
        this,
        'DiagnosticsDashboard',
        {
          dashboardName:
            'llm-parity-diagnostics',
        },
      );

    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title:
          'API Client / Authorization Errors',
        left: [
          api4xxMetric,
        ],
        width:
          12,
        height:
          6,
      }),

      new cloudwatch.GraphWidget({
        title:
          'Private ALB Target Response Time',
        left: [
          targetResponseTime,
        ],
        width:
          12,
        height:
          6,
      }),
    );
  }
}
