import * as cdk from 'aws-cdk-lib';
import {
  Match,
  Template,
} from 'aws-cdk-lib/assertions';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as sns from 'aws-cdk-lib/aws-sns';
import {
  applyStandardTags,
  COST_ALLOCATION_TAG_KEYS,
  STANDARD_TAGS,
} from '../lib/standard-tags';

const requiredTagArray = Object.entries(
  STANDARD_TAGS,
).map(([key, value]) => ({
  Key: key,
  Value: value,
}));

describe('standard cost-allocation tags', () => {
  test('defines the expected project tagging standard', () => {
    expect(STANDARD_TAGS).toEqual({
      Project:
        'aws-llm-production-parity-lab',
      Environment: 'dev',
      CostCenter: 'llm-parity-lab',
      ManagedBy: 'AWS-CDK',
      Repository:
        'Flem15/aws-llm-production-lab',
    });
  });

  test('defines the financial cost-allocation dimensions', () => {
    expect(
      COST_ALLOCATION_TAG_KEYS,
    ).toEqual([
      'Project',
      'Environment',
      'CostCenter',
    ]);
  });

  test('applies all standard tags to SNS resources', () => {
    const app = new cdk.App();

    const stack = new cdk.Stack(
      app,
      'StandardTagsSnsTestStack',
    );

    new sns.Topic(
      stack,
      'TaggedTopic',
      {
        topicName:
          'standard-tag-test-topic',
      },
    );

    applyStandardTags(app);

    const template =
      Template.fromStack(stack);

    for (const requiredTag of requiredTagArray) {
      template.hasResourceProperties(
        'AWS::SNS::Topic',
        {
          Tags: Match.arrayWith([
            requiredTag,
          ]),
        },
      );
    }
  });

  test('applies all standard tags to VPC resources', () => {
    const app = new cdk.App();

    const stack = new cdk.Stack(
      app,
      'StandardTagsVpcTestStack',
    );

    new ec2.Vpc(
      stack,
      'TaggedVpc',
      {
        maxAzs: 2,
        natGateways: 0,
      },
    );

    applyStandardTags(app);

    const template =
      Template.fromStack(stack);

    for (const requiredTag of requiredTagArray) {
      template.hasResourceProperties(
        'AWS::EC2::VPC',
        {
          Tags: Match.arrayWith([
            requiredTag,
          ]),
        },
      );
    }
  });

  test('applies the Project tag with the correct value', () => {
    const app = new cdk.App();

    const stack = new cdk.Stack(
      app,
      'ProjectTagTestStack',
    );

    new sns.Topic(stack, 'Topic');

    applyStandardTags(app);

    Template.fromStack(
      stack,
    ).hasResourceProperties(
      'AWS::SNS::Topic',
      {
        Tags: Match.arrayWith([
          {
            Key: 'Project',
            Value:
              'aws-llm-production-parity-lab',
          },
        ]),
      },
    );
  });

  test('applies the Environment tag with the correct value', () => {
    const app = new cdk.App();

    const stack = new cdk.Stack(
      app,
      'EnvironmentTagTestStack',
    );

    new sns.Topic(stack, 'Topic');

    applyStandardTags(app);

    Template.fromStack(
      stack,
    ).hasResourceProperties(
      'AWS::SNS::Topic',
      {
        Tags: Match.arrayWith([
          {
            Key: 'Environment',
            Value: 'dev',
          },
        ]),
      },
    );
  });

  test('applies the CostCenter tag with the correct value', () => {
    const app = new cdk.App();

    const stack = new cdk.Stack(
      app,
      'CostCenterTagTestStack',
    );

    new sns.Topic(stack, 'Topic');

    applyStandardTags(app);

    Template.fromStack(
      stack,
    ).hasResourceProperties(
      'AWS::SNS::Topic',
      {
        Tags: Match.arrayWith([
          {
            Key: 'CostCenter',
            Value: 'llm-parity-lab',
          },
        ]),
      },
    );
  });
});
