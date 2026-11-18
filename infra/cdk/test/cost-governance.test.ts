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
import {
  CostGovernanceStack,
} from '../lib/cost-governance-stack';

const monthlyBudgetLimit = 35;

function createTemplate(): Template {
  const app = new cdk.App();

  const stack = new CostGovernanceStack(
    app,
    'TestCostGovernanceStack',
    {
      monthlyBudgetLimit,
    },
  );

  return Template.fromStack(stack);
}

describe('cost governance', () => {
  const template = createTemplate();

  test('creates the dedicated budget-alert topic', () => {
    template.hasResourceProperties(
      'AWS::SNS::Topic',
      {
        TopicName:
          'llm-parity-dev-budget-alerts',
        DisplayName:
          'LLM Parity Dev Budget Alerts',
      },
    );
  });

  test('allows the AWS Budgets service to publish alerts', () => {
    template.hasResourceProperties(
      'AWS::SNS::TopicPolicy',
      {
        PolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Sid: 'AllowAwsBudgetsToPublish',
              Effect: 'Allow',
              Principal: {
                Service:
                  'budgets.amazonaws.com',
              },
              Action: 'sns:Publish',
              Resource: Match.anyValue(),
            }),
          ]),
        },
      },
    );
  });

  test('creates a monthly 35 dollar cost budget', () => {
    template.hasResourceProperties(
      'AWS::Budgets::Budget',
      {
        Budget: {
          BudgetName:
            'llm-parity-dev-monthly-cost-budget',
          BudgetType: 'COST',
          TimeUnit: 'MONTHLY',
          BudgetLimit: {
            Amount: 35,
            Unit: 'USD',
          },
        },
      },
    );
  });

  test('configures three budget notifications', () => {
    template.hasResourceProperties(
      'AWS::Budgets::Budget',
      {
        NotificationsWithSubscribers:
          Match.arrayWith([
            Match.objectLike({
              Notification: {
                ComparisonOperator:
                  'GREATER_THAN',
                NotificationType: 'ACTUAL',
                Threshold: 50,
                ThresholdType: 'PERCENTAGE',
              },
            }),
            Match.objectLike({
              Notification: {
                ComparisonOperator:
                  'GREATER_THAN',
                NotificationType: 'ACTUAL',
                Threshold: 80,
                ThresholdType: 'PERCENTAGE',
              },
            }),
            Match.objectLike({
              Notification: {
                ComparisonOperator:
                  'GREATER_THAN',
                NotificationType:
                  'FORECASTED',
                Threshold: 100,
                ThresholdType: 'PERCENTAGE',
              },
            }),
          ]),
      },
    );
  });

  test('sends every budget alert through SNS', () => {
    const budgets =
      template.findResources(
        'AWS::Budgets::Budget',
      );

    assert.strictEqual((Object.keys(budgets)).length, 1);

    const budget = Object.values(budgets)[0] as {
      Properties?: {
        NotificationsWithSubscribers?: Array<{
          Subscribers?: Array<{
            SubscriptionType?: string;
          }>;
        }>;
      };
    };

    const notifications =
      budget.Properties
        ?.NotificationsWithSubscribers ?? [];

    assert.strictEqual((notifications).length, 3);

    for (const notification of notifications) {
      {
assert.ok((notification.Subscribers ?? []).some((item: unknown) => matchesPartial(item, {
            SubscriptionType: 'SNS',
          })));
};
    }
  });

  test('rejects invalid monthly limits', () => {
    const app = new cdk.App();

    assert.throws(() =>
        new CostGovernanceStack(
          app,
          'InvalidCostGovernanceStack',
          {
            monthlyBudgetLimit: 0,
          },
        ), (error: unknown) => error instanceof Error && error.message.includes('monthlyBudgetLimit must be a positive number'));
  });
});
