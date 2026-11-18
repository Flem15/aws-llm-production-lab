import * as cdk from 'aws-cdk-lib';
import {
  Match,
  Template,
} from 'aws-cdk-lib/assertions';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import {
  IncidentResponseStack,
} from '../lib/incident-response-stack';

function createTemplate(): Template {
  const app = new cdk.App();

  const dependencyStack =
    new cdk.Stack(
      app,
      'IncidentTestDependencies',
      {
        env: {
          account:
            '111111111111',
          region:
            'us-east-2',
        },
      },
    );

  const vpc = new ec2.Vpc(
    dependencyStack,
    'Vpc',
    {
      maxAzs: 2,
      natGateways: 0,
    },
  );

  const cluster =
    new ecs.Cluster(
      dependencyStack,
      'Cluster',
      {
        vpc,
        clusterName:
          'llm-parity-cluster',
      },
    );

  const stack =
    new IncidentResponseStack(
      app,
      'IncidentResponseTestStack',
      {
        env: {
          account:
            '111111111111',
          region:
            'us-east-2',
        },
        cluster,
      },
    );

  return Template.fromStack(stack);
}

describe(
  'incident response alerting',
  () => {
    const template =
      createTemplate();

    test(
      'creates the dedicated incident SNS topic',
      () => {
        template.hasResourceProperties(
          'AWS::SNS::Topic',
          {
            TopicName:
              'llm-parity-dev-incident-alerts',
            DisplayName:
              'LLM Parity Dev Incident Alerts',
          },
        );

        const rules =
          template.findResources(
            'AWS::Events::Rule',
          );

        const operationalRule =
          Object.values(rules).find(
            (resource) =>
              resource.Properties?.Name ===
              'llm-parity-operational-alarm-alert',
          );

        if (!operationalRule) {
          throw new Error(
            'Operational alarm EventBridge rule was not found',
          );
        }

        const alarmNames =
          operationalRule.Properties
            ?.EventPattern
            ?.detail
            ?.alarmName;

        expect(alarmNames).toEqual(
          expect.arrayContaining([
            'llm-parity-api-5xx',
            'llm-parity-ecs-high-cpu',
          ]),
        );
      },
    );

    test(
      'routes operational CloudWatch alarms only when they enter ALARM',
      () => {
        template.hasResourceProperties(
          'AWS::Events::Rule',
          {
            Name:
              'llm-parity-operational-alarm-alert',
            EventPattern:
              Match.objectLike({
                source: [
                  'aws.cloudwatch',
                ],
                'detail-type': [
                  'CloudWatch Alarm State Change',
                ],
                detail:
                  Match.objectLike({
                    alarmName:
                      Match.anyValue(),
                    state: {
                      value: [
                        'ALARM',
                      ],
                    },
                  }),
              }),
            Targets:
              Match.arrayWith([
                Match.objectLike({
                  Arn:
                    Match.anyValue(),
                }),
              ]),
          },
        );
      },
    );

    test(
      'alerts on abnormal ECS task stops but excludes service scheduler stops',
      () => {
        template.hasResourceProperties(
          'AWS::Events::Rule',
          {
            Name:
              'llm-parity-abnormal-task-stop',
            EventPattern:
              Match.objectLike({
                source: [
                  'aws.ecs',
                ],
                'detail-type': [
                  'ECS Task State Change',
                ],
                detail:
                  Match.objectLike({
                    lastStatus: [
                      'STOPPED',
                    ],
                    stopCode: [
                      {
                        'anything-but':
                          'ServiceSchedulerInitiated',
                      },
                    ],
                  }),
              }),
          },
        );
      },
    );

    test(
      'alerts when an ECS deployment fails',
      () => {
        template.hasResourceProperties(
          'AWS::Events::Rule',
          {
            Name:
              'llm-parity-deployment-failure',
            EventPattern:
              Match.objectLike({
                source: [
                  'aws.ecs',
                ],
                'detail-type': [
                  'ECS Deployment State Change',
                ],
                detail:
                  Match.objectLike({
                    eventName: [
                      'SERVICE_DEPLOYMENT_FAILED',
                    ],
                  }),
              }),
          },
        );
      },
    );

    test('publisher policies permit only the topic and encryption key', () => {
      template.hasResourceProperties('AWS::IAM::Policy', {
        PolicyDocument: { Statement: Match.arrayWith([
          Match.objectLike({ Effect: 'Allow', Action: 'sns:Publish', Resource: Match.anyValue() }),
        ]) },
      });
      template.resourceCountIs('AWS::SNS::TopicPolicy', 0);
    });

    test(
      'routes diagnostic alarms to incident response',
      () => {
        const rules =
          template.findResources(
            'AWS::Events::Rule',
          );

        const serialized =
          JSON.stringify(
            rules,
          );

        expect(
          serialized,
        ).toContain(
          'llm-parity-api-4xx',
        );

        expect(
          serialized,
        ).toContain(
          'llm-parity-alb-high-target-response-time',
        );
      },
    );

  },
);

test('development notifications encrypt retained key and use scoped EventBridge publisher roles', () => {
  const template = createTemplate();
  template.hasResource('AWS::KMS::Key', { DeletionPolicy: 'Retain', Properties: Match.objectLike({ EnableKeyRotation: true }) });
  template.hasResourceProperties('AWS::SNS::Topic', { KmsMasterKeyId: Match.anyValue() });
  const resources = template.toJSON().Resources;
  const rules = Object.values(resources).filter((r: any) => r.Type === 'AWS::Events::Rule') as any[];
  expect(rules).toHaveLength(3);
  for (const rule of rules) expect(rule.Properties.Targets[0].RoleArn).toBeDefined();
  const roles = Object.values(resources).filter((r: any) => r.Type === 'AWS::IAM::Role') as any[];
  expect(roles).toHaveLength(3);
  for (const role of roles) {
    const trust = role.Properties.AssumeRolePolicyDocument.Statement[0];
    expect(trust.Principal.Service).toEqual('events.amazonaws.com');
    expect(trust.Condition.StringEquals['aws:SourceAccount']).toEqual('111111111111');
    expect(trust.Condition.ArnEquals['aws:SourceArn']).toBeDefined();
  }
  const policies = Object.values(resources).filter((r: any) => r.Type === 'AWS::IAM::Policy') as any[];
  for (const policy of policies) for (const statement of policy.Properties.PolicyDocument.Statement) {
    expect(statement.Resource).not.toEqual('*');
  }
});
