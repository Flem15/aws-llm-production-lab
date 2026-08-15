import * as cdk from 'aws-cdk-lib';
import {
  Template,
} from 'aws-cdk-lib/assertions';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import {
  DiagnosticsStack,
} from '../lib/diagnostics-stack';

function createTemplate(): Template {
  const app =
    new cdk.App();

  const dependencies =
    new cdk.Stack(
      app,
      'DiagnosticsDependencies',
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
        natGateways: 0,
      },
    );

  const loadBalancer =
    new elbv2
      .ApplicationLoadBalancer(
        dependencies,
        'Alb',
        {
          vpc,
          internetFacing:
            false,
        },
      );

  const httpApi =
    new apigwv2.HttpApi(
      dependencies,
      'Api',
    );

  const stack =
    new DiagnosticsStack(
      app,
      'DiagnosticsTestStack',
      {
        env: {
          account:
            '111111111111',
          region:
            'us-east-2',
        },
        httpApi,
        loadBalancer,
      },
    );

  return Template.fromStack(
    stack,
  );
}

describe(
  'production diagnostics',
  () => {
    const template =
      createTemplate();

    test(
      'creates API 4xx alarm',
      () => {
        template.hasResourceProperties(
          'AWS::CloudWatch::Alarm',
          {
            AlarmName:
              'llm-parity-api-4xx',
            Threshold:
              10,
            TreatMissingData:
              'notBreaching',
          },
        );

        const alarms =
          template.findResources(
            'AWS::CloudWatch::Alarm',
          ) as Record<
            string,
            {
              Properties?: {
                AlarmName?: string;
              };
            }
          >;

        const apiAlarm =
          Object.values(alarms).find(
            (resource) =>
              resource.Properties
                ?.AlarmName ===
              'llm-parity-api-4xx',
          );

        expect(apiAlarm).toBeDefined();

        const serialized =
          JSON.stringify(apiAlarm);

        expect(serialized).toContain(
          'AWS/ApiGateway',
        );

        expect(serialized).toContain(
          '4xx',
        );
      },
    );

    test(
      'creates ALB target response-time alarm',
      () => {
        template.hasResourceProperties(
          'AWS::CloudWatch::Alarm',
          {
            AlarmName:
              'llm-parity-alb-high-target-response-time',
            Threshold:
              2,
            TreatMissingData:
              'notBreaching',
          },
        );

        const alarms =
          template.findResources(
            'AWS::CloudWatch::Alarm',
          ) as Record<
            string,
            {
              Properties?: {
                AlarmName?: string;
              };
            }
          >;

        const albAlarm =
          Object.values(alarms).find(
            (resource) =>
              resource.Properties
                ?.AlarmName ===
              'llm-parity-alb-high-target-response-time',
          );

        expect(albAlarm).toBeDefined();

        const serialized =
          JSON.stringify(albAlarm);

        expect(serialized).toContain(
          'AWS/ApplicationELB',
        );

        expect(serialized).toContain(
          'TargetResponseTime',
        );
      },
    );

    test(
      'creates diagnostics dashboard',
      () => {
        template.hasResourceProperties(
          'AWS::CloudWatch::Dashboard',
          {
            DashboardName:
              'llm-parity-diagnostics',
          },
        );

        const dashboards =
          template.findResources(
            'AWS::CloudWatch::Dashboard',
          );

        const serialized =
          JSON.stringify(dashboards);

        expect(serialized).toContain(
          'API Client / Authorization Errors',
        );

        expect(serialized).toContain(
          'Private ALB Target Response Time',
        );
      },
    );
  },
);
