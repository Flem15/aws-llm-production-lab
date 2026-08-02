import {
  CfnOutput,
  Stack,
  StackProps,
} from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';

export interface CostGovernanceStackProps
  extends StackProps {
  readonly monthlyBudgetLimit: number;
}

export class CostGovernanceStack extends Stack {
  public readonly budgetAlertsTopic: sns.Topic;
  public readonly budgetName: string;

  constructor(
    scope: Construct,
    id: string,
    props: CostGovernanceStackProps,
  ) {
    super(scope, id, props);

    if (
      !Number.isFinite(props.monthlyBudgetLimit) ||
      props.monthlyBudgetLimit <= 0
    ) {
      throw new Error(
        'monthlyBudgetLimit must be a positive number',
      );
    }

    this.budgetName =
      'llm-parity-dev-monthly-cost-budget';

    this.budgetAlertsTopic = new sns.Topic(
      this,
      'BudgetAlertsTopic',
      {
        topicName: 'llm-parity-dev-budget-alerts',
        displayName: 'LLM Parity Dev Budget Alerts',
      },
    );

    this.budgetAlertsTopic.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowAwsBudgetsToPublish',
        effect: iam.Effect.ALLOW,
        principals: [
          new iam.ServicePrincipal(
            'budgets.amazonaws.com',
          ),
        ],
        actions: ['sns:Publish'],
        resources: [
          this.budgetAlertsTopic.topicArn,
        ],
      }),
    );

    const snsSubscriber:
      budgets.CfnBudget.SubscriberProperty = {
        address: this.budgetAlertsTopic.topicArn,
        subscriptionType: 'SNS',
      };

    new budgets.CfnBudget(
      this,
      'MonthlyCostBudget',
      {
        budget: {
          budgetName: this.budgetName,
          budgetType: 'COST',
          timeUnit: 'MONTHLY',
          budgetLimit: {
            amount:
              props.monthlyBudgetLimit,
            unit: 'USD',
          },
        },
        notificationsWithSubscribers: [
          {
            notification: {
              comparisonOperator: 'GREATER_THAN',
              notificationType: 'ACTUAL',
              threshold: 50,
              thresholdType: 'PERCENTAGE',
            },
            subscribers: [snsSubscriber],
          },
          {
            notification: {
              comparisonOperator: 'GREATER_THAN',
              notificationType: 'ACTUAL',
              threshold: 80,
              thresholdType: 'PERCENTAGE',
            },
            subscribers: [snsSubscriber],
          },
          {
            notification: {
              comparisonOperator: 'GREATER_THAN',
              notificationType: 'FORECASTED',
              threshold: 100,
              thresholdType: 'PERCENTAGE',
            },
            subscribers: [snsSubscriber],
          },
        ],
      },
    );

    new CfnOutput(
      this,
      'BudgetAlertsTopicArn',
      {
        description:
          'SNS topic used for AWS Budget alerts',
        value: this.budgetAlertsTopic.topicArn,
      },
    );

    new CfnOutput(
      this,
      'MonthlyBudgetName',
      {
        description:
          'Name of the monthly AWS cost budget',
        value: this.budgetName,
      },
    );

    new CfnOutput(
      this,
      'MonthlyBudgetLimitUsd',
      {
        description:
          'Configured monthly AWS cost limit in USD',
        value:
          props.monthlyBudgetLimit.toFixed(2),
      },
    );
  }
}
