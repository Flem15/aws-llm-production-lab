import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { isDeepStrictEqual } from 'node:util';
import './test-support';
import { matchesPartial } from './test-support';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { BotCityStack, BotCityProps } from '../lib/bot-city-stack';

const props: BotCityProps = { env: { account: '655469962246', region: 'us-east-2' },
  imageDigest: `sha256:${'a'.repeat(64)}`,
  secretArn: 'arn:aws:secretsmanager:us-east-2:655469962246:secret:bot-city-paper-Ab1234',
  paperAccountId: 'test-paper-account' };
const template = () => Template.fromStack(new BotCityStack(new cdk.App(), 'Test', props));

test('offline isolated architecture has no service or Internet route', () => {
  const t = template();
  t.resourceCountIs('AWS::EC2::NatGateway', 0);
  t.resourceCountIs('AWS::EC2::InternetGateway', 0);
  t.resourceCountIs('AWS::ECS::Service', 0);
  t.resourceCountIs('AWS::DynamoDB::Table', 3);
  t.resourceCountIs('AWS::IAM::Role', 4);
  assert.ok(!(JSON.stringify(t.toJSON())).includes('AWSLambdaBasicExecutionRole'));
  t.resourceCountIs('AWS::EC2::VPCEndpoint', 6);
  t.resourceCountIs('AWS::ECS::TaskDefinition', 1);
  const tasks = Object.values(t.findResources('AWS::ECS::TaskDefinition')) as any[];
  const container = tasks[0].Properties.ContainerDefinitions[0];
  assert.ok((container.Environment).some((item: unknown) => isDeepStrictEqual(item, { Name: 'BOT_CITY_TRADING_ENABLED', Value: 'false' })));
  assert.strictEqual(container.StopTimeout, 120);
  assert.deepStrictEqual(container.Secrets.map((s: any) => s.Name).sort(), ['ALPACA_API_KEY', 'ALPACA_SECRET_KEY']);
  assert.ok((JSON.stringify(tasks)).includes('@sha256:'));
  for (const r of Object.values(t.findResources('AWS::DynamoDB::Table')) as any[]) {
    assert.strictEqual(r.DeletionPolicy, 'Retain');
    assert.strictEqual(r.Properties.DeletionProtectionEnabled, true);
    assert.strictEqual(r.Properties.PointInTimeRecoverySpecification.PointInTimeRecoveryEnabled, true);
    assert.strictEqual(r.Properties.TimeToLiveSpecification, undefined);
  }
});

test('runtime role scopes data permissions; only execution role reads injected secret', () => {
  const t = template();
  const policies = Object.entries(t.findResources('AWS::IAM::Policy'));
  const runtime = policies.find(([name]) => name.startsWith('TaskRoleDefaultPolicy'))![1] as any;
  assert.strictEqual((runtime.Properties.PolicyDocument.Statement).length, 3);
  for (const statement of runtime.Properties.PolicyDocument.Statement) {
    assert.ok(!(JSON.stringify(statement.Resource)).includes('*'));
    assert.strictEqual(statement.Conditions, undefined);
    assert.strictEqual((statement.Condition['ForAllValues:StringEquals']['dynamodb:LeadingKeys']).length, 1);
    for (const action of statement.Action) {
      assert.doesNotMatch(action, /Scan|secretsmanager|sts:|iam:|ecs:/);
    }
  }
  const execution = policies.find(([name]) => name.startsWith('ExecutionRoleDefaultPolicy'))![1] as any;
  const secret = execution.Properties.PolicyDocument.Statement.find((s: any) =>
    JSON.stringify(s.Action).includes('secretsmanager:GetSecretValue'));
  assert.strictEqual(secret.Resource, props.secretArn);
  assert.ok(!(JSON.stringify(runtime)).includes('DeleteItem","dynamodb:GetItem'));
});

test('configuration refuses mutable images, production and unreviewed egress', () => {
  for (const overrides of [{ imageDigest: 'latest' }, { secretArn: '*' },
    { env: { account: '111111111111', region: 'us-east-2' } },
    { egress: { reviewId: '', proxyImageDigest: `sha256:${'b'.repeat(64)}` } }]) {
    assert.throws(() => new BotCityStack(new cdk.App(), 'Bad', { ...props, ...overrides }));
  }
});

test('reviewed managed proxy stays at zero; trading remains disabled', () => {
  const t = Template.fromStack(new BotCityStack(new cdk.App(), 'Proxy', { ...props,
    egress: { reviewId: 'review-18', proxyImageDigest: `sha256:${'b'.repeat(64)}` } }));
  t.resourceCountIs('AWS::EC2::NatGateway', 1);
  t.resourceCountIs('AWS::ECS::Service', 1);
  assert.ok((JSON.stringify(t.toJSON())).includes('3128'));
  assert.ok((JSON.stringify(t.toJSON())).includes('"BOT_CITY_TRADING_ENABLED","Value":"false"'));
});

const managed = () => Template.fromStack(new BotCityStack(new cdk.App(), 'Test', {
  ...props, egress: { reviewId: 'day19-reviewed', proxyImageDigest: `sha256:${'b'.repeat(64)}` },
}));

test('managed egress preserves isolated CIDRs and has no task default Internet route', () => {
  const base = template().toJSON().Resources;
  const t = managed();
  const resources = t.toJSON().Resources;
  for (const [id, resource] of Object.entries(base) as any[]) {
    if (['AWS::EC2::VPC', 'AWS::EC2::Subnet', 'AWS::EC2::RouteTable',
      'AWS::EC2::SubnetRouteTableAssociation'].includes(resource.Type)) {
      assert.deepStrictEqual(resources[id].Properties, resource.Properties);
    }
  }
  for (const [id, route] of Object.entries(t.findResources('AWS::EC2::Route')) as any[]) {
    assert.match(id, /^Egress/);
    assert.ok(!(JSON.stringify(route.Properties.RouteTableId)).includes('BotCityIsolated'));
  }
  const groups = t.findResources('AWS::EC2::SecurityGroup') as any;
  const task = Object.entries(groups).find(([id]) => id.startsWith('TaskSecurityGroup'))![1] as any;
  assert.ok(!(JSON.stringify(task.Properties.SecurityGroupEgress || [])).includes('0.0.0.0/0'));
  for (const rule of Object.values(t.findResources('AWS::EC2::SecurityGroupEgress')) as any[]) {
    if (JSON.stringify(rule.Properties.GroupId).includes('TaskSecurityGroup')) {
      assert.strictEqual(rule.Properties.CidrIp, undefined);
      assert.strictEqual(rule.Properties.CidrIpv6, undefined);
      assert.ok(([443, 3128]).includes(rule.Properties.FromPort));
    }
  }
  const ingress = Object.values(t.findResources('AWS::EC2::SecurityGroupIngress')) as any[];
  const proxyIngress = ingress.find(r => r.Properties.FromPort === 3128).Properties;
  assert.ok((JSON.stringify(proxyIngress.SourceSecurityGroupId)).includes('TaskSecurityGroup'));
  assert.strictEqual(proxyIngress.CidrIp, undefined);
});

test('proxy has no data or secrets permissions and starts at zero in private subnet', () => {
  const t = managed();
  const service = Object.values(t.findResources('AWS::ECS::Service'))[0] as any;
  assert.strictEqual(service.Properties.DesiredCount, 0);
  assert.strictEqual(service.Properties.EnableExecuteCommand, false);
  assert.strictEqual(service.Properties.NetworkConfiguration.AwsvpcConfiguration.AssignPublicIp, 'DISABLED');
  assert.ok((JSON.stringify(service.Properties.NetworkConfiguration)).includes('EgressProxySubnet'));
  const roles = t.findResources('AWS::IAM::Role');
  const proxyRole = Object.entries(roles).find(([id]) => id.startsWith('ProxyTaskRole'))![1] as any;
  assert.strictEqual(proxyRole.Properties.Policies, undefined);
  assert.strictEqual(proxyRole.Properties.ManagedPolicyArns, undefined);
  const execution = Object.entries(t.findResources('AWS::IAM::Policy')).find(([id]) => id.startsWith('ProxyExecutionRole'))![1] as any;
  assert.doesNotMatch(JSON.stringify(execution), /secretsmanager|dynamodb|sts:|iam:PassRole/);
  const definition = Object.entries(t.findResources('AWS::ECS::TaskDefinition')).find(([id]) => id.startsWith('ProxyTask'))![1] as any;
  const container = definition.Properties.ContainerDefinitions[0];
  assert.strictEqual(container.ReadonlyRootFilesystem, true);
  assert.strictEqual(container.Secrets, undefined);
  assert.strictEqual(container.User, 'proxyworker');
  assert.notStrictEqual(container.HealthCheck, undefined);
});

test('encrypted alarm topic supports CloudWatch and EventBridge without giving tasks KMS access', () => {
  const t = managed();
  const topic = Object.values(t.findResources('AWS::SNS::Topic'))[0] as any;
  assert.notStrictEqual(topic.Properties.KmsMasterKeyId, undefined);
  const key = Object.values(t.findResources('AWS::KMS::Key'))[0] as any;
  assert.strictEqual(key.Properties.EnableKeyRotation, true);
  assert.strictEqual(key.DeletionPolicy, 'Retain');
  assert.ok((JSON.stringify(key.Properties.KeyPolicy)).includes('cloudwatch.amazonaws.com'));
  assert.ok(!(JSON.stringify(key.Properties.KeyPolicy)).includes('events.amazonaws.com'));
  const publisher = Object.entries(t.findResources('AWS::IAM::Role')).find(([id]) => id.startsWith('StoppedEventPublisher'))![1] as any;
  assert.ok((JSON.stringify(publisher.Properties.AssumeRolePolicyDocument)).includes('aws:SourceArn'));
  for (const [id, policy] of Object.entries(t.findResources('AWS::IAM::Policy'))) {
    if (/^(TaskRole|ProxyTaskRole)/.test(id)) assert.ok(!(JSON.stringify(policy)).includes('kms:'));
  }
  t.resourceCountIs('AWS::CloudWatch::Alarm', 9);
  for (const alarm of Object.values(t.findResources('AWS::CloudWatch::Alarm')) as any[]) {
    assert.strictEqual((alarm.Properties.AlarmActions).length, 1);
  }
});

test('AWS endpoints cannot provide arbitrary bucket, secret or database access', () => {
  const t = managed();
  const endpoints = t.findResources('AWS::EC2::VPCEndpoint');
  for (const endpoint of Object.values(endpoints) as any[]) {
    assert.notStrictEqual(endpoint.Properties.PolicyDocument, undefined);
    const statements = endpoint.Properties.PolicyDocument.Statement;
    for (const statement of statements) {
      if (JSON.stringify(statement.Principal) === '{"AWS":"*"}') {
        if (statement.Action === 's3:GetObject') {
          assert.ok((statement.Resource).includes('prod-us-east-2-starport-layer-bucket/'));
        } else {
          assert.ok((JSON.stringify(statement.Condition)).includes('aws:PrincipalArn'));
          assert.ok((JSON.stringify(statement.Condition)).includes('TaskRole'));
        }
      }
    }
  }
  const secret = Object.entries(endpoints).find(([id]) => id.startsWith('VpcSecrets'))![1] as any;
  assert.ok(!(JSON.stringify(secret.Properties.PolicyDocument)).includes('ProxyExecutionRole'));
  assert.ok((JSON.stringify(secret.Properties.PolicyDocument)).includes(props.secretArn));
});
