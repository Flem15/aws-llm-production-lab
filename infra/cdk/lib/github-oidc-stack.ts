import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';

export interface GitHubOidcStackProps extends cdk.StackProps {
  repository: ecr.IRepository;
  httpApi: apigwv2.HttpApi;
  taskDefinition: ecs.FargateTaskDefinition;
}

export class GitHubOidcStack extends cdk.Stack {
  public readonly actionsRole: iam.Role;

  constructor(
    scope: Construct,
    id: string,
    props: GitHubOidcStackProps,
  ) {
    super(scope, id, props);

    const provider = new iam.OpenIdConnectProvider(
      this,
      'GitHubActionsOidcProvider',
      {
        url: 'https://token.actions.githubusercontent.com',
        clientIds: ['sts.amazonaws.com'],
      },
    );

    const githubPrincipal = new iam.WebIdentityPrincipal(
      provider.openIdConnectProviderArn,
      {
        StringEquals: {
          'token.actions.githubusercontent.com:aud':
            'sts.amazonaws.com',
          'token.actions.githubusercontent.com:sub':
            'repo:Flem15/aws-llm-production-lab:environment:dev',
        },
      },
    );

    this.actionsRole = new iam.Role(
      this,
      'GitHubActionsEcrRole',
      {
        roleName: 'llm-parity-actions-ecr-push',
        description:
          'Allows the approved GitHub Actions dev environment to push immutable images to ECR.',
        assumedBy: githubPrincipal,
        maxSessionDuration: cdk.Duration.hours(1),
      },
    );

    // A resource-based bootstrap trust must not bypass release-role restrictions.
    // This deny also protects against accidental future identity-policy grants.
    this.actionsRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DenyInfrastructureEscalation',
      effect: iam.Effect.DENY,
      actions: ['sts:AssumeRole', 'cloudformation:*'],
      resources: ['*'],
    }));

    props.repository.grantPullPush(this.actionsRole);

    props.repository.grant(
      this.actionsRole,
      'ecr:DescribeImages',
    );

    const stack = cdk.Stack.of(this);
    const serviceArn = stack.formatArn({
      service: 'ecs',
      resource: 'service',
      resourceName: 'llm-parity-cluster/llm-parity-demo-inference-service',
    });

    this.actionsRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InspectAndUpdateEcsRelease',
        actions: ['ecs:DescribeServices', 'ecs:UpdateService'],
        resources: [serviceArn],
      }),
    );

    this.actionsRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InspectReleaseTasks',
        actions: ['ecs:DescribeTaskDefinition', 'ecs:ListTasks', 'ecs:DescribeTasks'],
        resources: ['*'],
      }),
    );

    this.actionsRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InspectReleaseHealth',
        actions: [
          'elasticloadbalancing:DescribeLoadBalancers',
          'elasticloadbalancing:DescribeTargetGroups',
          'elasticloadbalancing:DescribeTargetHealth',
          'apigateway:GET',
        ],
        resources: ['*'],
      }),
    );

    const protectedInvokeRouteArn =
      cdk.Stack.of(this).formatArn({
        service: 'execute-api',
        resource: props.httpApi.httpApiId,
        resourceName: '$default/POST/invoke',
        arnFormat:
          cdk.ArnFormat.SLASH_RESOURCE_NAME,
      });

    this.actionsRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeProtectedInferenceRoute',
        actions: ['execute-api:Invoke'],
        resources: [protectedInvokeRouteArn],
      }),
    );

    this.actionsRole.addToPolicy(new iam.PolicyStatement({
      actions: ['ecs:RegisterTaskDefinition'],
      resources: ['*'],
    }));
    this.actionsRole.addToPolicy(new iam.PolicyStatement({
      actions: ['iam:PassRole'],
      resources: [
        props.taskDefinition.taskRole.roleArn,
        props.taskDefinition.obtainExecutionRole().roleArn,
      ],
      conditions: { StringEquals: { 'iam:PassedToService': 'ecs-tasks.amazonaws.com' } },
    }));

    new cdk.CfnOutput(this, 'GitHubActionsRoleName', {
      value: this.actionsRole.roleName,
    });
  }
}
