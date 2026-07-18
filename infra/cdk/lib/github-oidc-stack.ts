import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';

export interface GitHubOidcStackProps extends cdk.StackProps {
  repository: ecr.IRepository;
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

    props.repository.grantPullPush(this.actionsRole);

    props.repository.grant(
      this.actionsRole,
      'ecr:DescribeImages',
    );

    this.actionsRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InspectAndUpdateEcsRelease',
        actions: [
          'ecs:DescribeServices',
          'ecs:DescribeTaskDefinition',
          'ecs:ListTasks',
          'ecs:DescribeTasks',
          'ecs:UpdateService',
        ],
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

    const bootstrapRoleArns = [
      cdk.Stack.of(this).formatArn({
        service: 'iam',
        region: '',
        resource: 'role',
        resourceName: 'cdk-prod01-deploy-role-*',
      }),
      cdk.Stack.of(this).formatArn({
        service: 'iam',
        region: '',
        resource: 'role',
        resourceName: 'cdk-prod01-file-publishing-role-*',
      }),
      cdk.Stack.of(this).formatArn({
        service: 'iam',
        region: '',
        resource: 'role',
        resourceName: 'cdk-prod01-lookup-role-*',
      }),
    ];

    this.actionsRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AssumeCdkBootstrapRoles',
        actions: ['sts:AssumeRole'],
        resources: bootstrapRoleArns,
      }),
    );

    new cdk.CfnOutput(this, 'GitHubActionsRoleName', {
      value: this.actionsRole.roleName,
    });
  }
}
