import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { isDeepStrictEqual } from 'node:util';
import './test-support';
import { matchesPartial } from './test-support';
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

        {
assert.ok((alarmNames).some((item: unknown) => isDeepStrictEqual(item, 'llm-parity-api-5xx')));
assert.ok((alarmNames).some((item: unknown) => isDeepStrictEqual(item, 'llm-parity-ecs-high-cpu')));
};
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

        assert.ok((serialized).includes('llm-parity-api-4xx'));

        assert.ok((serialized).includes('llm-parity-alb-high-target-response-time'));
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
  assert.strictEqual((rules).length, 3);
  for (const rule of rules) assert.notStrictEqual(rule.Properties.Targets[0].RoleArn, undefined);
  const roles = Object.values(resources).filter((r: any) => r.Type === 'AWS::IAM::Role') as any[];
  assert.strictEqual((roles).length, 3);
  for (const role of roles) {
    const trust = role.Properties.AssumeRolePolicyDocument.Statement[0];
    assert.deepStrictEqual(trust.Principal.Service, 'events.amazonaws.com');
    assert.deepStrictEqual(trust.Condition.StringEquals['aws:SourceAccount'], '111111111111');
    assert.notStrictEqual(trust.Condition.ArnEquals['aws:SourceArn'], undefined);
  }
  const policies = Object.values(resources).filter((r: any) => r.Type === 'AWS::IAM::Policy') as any[];
  for (const policy of policies) for (const statement of policy.Properties.PolicyDocument.Statement) {
    assert.notDeepStrictEqual(statement.Resource, '*');
  }
});
