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
  expect(JSON.stringify(t.toJSON())).not.toContain('AWSLambdaBasicExecutionRole');
  t.resourceCountIs('AWS::EC2::VPCEndpoint', 6);
  t.resourceCountIs('AWS::ECS::TaskDefinition', 1);
  const tasks = Object.values(t.findResources('AWS::ECS::TaskDefinition')) as any[];
  const container = tasks[0].Properties.ContainerDefinitions[0];
  expect(container.Environment).toContainEqual({ Name: 'BOT_CITY_TRADING_ENABLED', Value: 'false' });
  expect(container.StopTimeout).toBe(120);
  expect(container.Secrets.map((s: any) => s.Name).sort()).toEqual(['ALPACA_API_KEY', 'ALPACA_SECRET_KEY']);
  expect(JSON.stringify(tasks)).toContain('@sha256:');
  for (const r of Object.values(t.findResources('AWS::DynamoDB::Table')) as any[]) {
    expect(r.DeletionPolicy).toBe('Retain');
    expect(r.Properties.DeletionProtectionEnabled).toBe(true);
    expect(r.Properties.PointInTimeRecoverySpecification.PointInTimeRecoveryEnabled).toBe(true);
    expect(r.Properties.TimeToLiveSpecification).toBeUndefined();
  }
});

test('runtime role scopes data permissions; only execution role reads injected secret', () => {
  const t = template();
  const policies = Object.entries(t.findResources('AWS::IAM::Policy'));
  const runtime = policies.find(([name]) => name.startsWith('TaskRoleDefaultPolicy'))![1] as any;
  expect(runtime.Properties.PolicyDocument.Statement).toHaveLength(3);
  for (const statement of runtime.Properties.PolicyDocument.Statement) {
    expect(JSON.stringify(statement.Resource)).not.toContain('*');
    expect(statement.Conditions).toBeUndefined();
    expect(statement.Condition['ForAllValues:StringEquals']['dynamodb:LeadingKeys']).toHaveLength(1);
    for (const action of statement.Action) {
      expect(action).not.toMatch(/Scan|secretsmanager|sts:|iam:|ecs:/);
    }
  }
  const execution = policies.find(([name]) => name.startsWith('ExecutionRoleDefaultPolicy'))![1] as any;
  const secret = execution.Properties.PolicyDocument.Statement.find((s: any) =>
    JSON.stringify(s.Action).includes('secretsmanager:GetSecretValue'));
  expect(secret.Resource).toBe(props.secretArn);
  expect(JSON.stringify(runtime)).not.toContain('DeleteItem","dynamodb:GetItem');
});

test('configuration refuses mutable images, production and unreviewed egress', () => {
  for (const overrides of [{ imageDigest: 'latest' }, { secretArn: '*' },
    { env: { account: '111111111111', region: 'us-east-2' } },
    { egress: { reviewId: '', proxyImageDigest: `sha256:${'b'.repeat(64)}` } }]) {
    expect(() => new BotCityStack(new cdk.App(), 'Bad', { ...props, ...overrides })).toThrow();
  }
});

test('reviewed managed proxy stays at zero; trading remains disabled', () => {
  const t = Template.fromStack(new BotCityStack(new cdk.App(), 'Proxy', { ...props,
    egress: { reviewId: 'review-18', proxyImageDigest: `sha256:${'b'.repeat(64)}` } }));
  t.resourceCountIs('AWS::EC2::NatGateway', 1);
  t.resourceCountIs('AWS::ECS::Service', 1);
  expect(JSON.stringify(t.toJSON())).toContain('3128');
  expect(JSON.stringify(t.toJSON())).toContain('"BOT_CITY_TRADING_ENABLED","Value":"false"');
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
      expect(resources[id].Properties).toEqual(resource.Properties);
    }
  }
  for (const [id, route] of Object.entries(t.findResources('AWS::EC2::Route')) as any[]) {
    expect(id).toMatch(/^Egress/);
    expect(JSON.stringify(route.Properties.RouteTableId)).not.toContain('BotCityIsolated');
  }
  const groups = t.findResources('AWS::EC2::SecurityGroup') as any;
  const task = Object.entries(groups).find(([id]) => id.startsWith('TaskSecurityGroup'))![1] as any;
  expect(JSON.stringify(task.Properties.SecurityGroupEgress || [])).not.toContain('0.0.0.0/0');
  for (const rule of Object.values(t.findResources('AWS::EC2::SecurityGroupEgress')) as any[]) {
    if (JSON.stringify(rule.Properties.GroupId).includes('TaskSecurityGroup')) {
      expect(rule.Properties.CidrIp).toBeUndefined();
      expect(rule.Properties.CidrIpv6).toBeUndefined();
      expect([443, 3128]).toContain(rule.Properties.FromPort);
    }
  }
  const ingress = Object.values(t.findResources('AWS::EC2::SecurityGroupIngress')) as any[];
  const proxyIngress = ingress.find(r => r.Properties.FromPort === 3128).Properties;
  expect(JSON.stringify(proxyIngress.SourceSecurityGroupId)).toContain('TaskSecurityGroup');
  expect(proxyIngress.CidrIp).toBeUndefined();
});

test('proxy has no data or secrets permissions and starts at zero in private subnet', () => {
  const t = managed();
  const service = Object.values(t.findResources('AWS::ECS::Service'))[0] as any;
  expect(service.Properties.DesiredCount).toBe(0);
  expect(service.Properties.EnableExecuteCommand).toBe(false);
  expect(service.Properties.NetworkConfiguration.AwsvpcConfiguration.AssignPublicIp).toBe('DISABLED');
  expect(JSON.stringify(service.Properties.NetworkConfiguration)).toContain('EgressProxySubnet');
  const roles = t.findResources('AWS::IAM::Role');
  const proxyRole = Object.entries(roles).find(([id]) => id.startsWith('ProxyTaskRole'))![1] as any;
  expect(proxyRole.Properties.Policies).toBeUndefined();
  expect(proxyRole.Properties.ManagedPolicyArns).toBeUndefined();
  const execution = Object.entries(t.findResources('AWS::IAM::Policy')).find(([id]) => id.startsWith('ProxyExecutionRole'))![1] as any;
  expect(JSON.stringify(execution)).not.toMatch(/secretsmanager|dynamodb|sts:|iam:PassRole/);
  const definition = Object.entries(t.findResources('AWS::ECS::TaskDefinition')).find(([id]) => id.startsWith('ProxyTask'))![1] as any;
  const container = definition.Properties.ContainerDefinitions[0];
  expect(container.ReadonlyRootFilesystem).toBe(true);
  expect(container.Secrets).toBeUndefined();
  expect(container.User).toBe('proxyworker');
  expect(container.HealthCheck).toBeDefined();
});

test('encrypted alarm topic supports CloudWatch and EventBridge without giving tasks KMS access', () => {
  const t = managed();
  const topic = Object.values(t.findResources('AWS::SNS::Topic'))[0] as any;
  expect(topic.Properties.KmsMasterKeyId).toBeDefined();
  const key = Object.values(t.findResources('AWS::KMS::Key'))[0] as any;
  expect(key.Properties.EnableKeyRotation).toBe(true);
  expect(key.DeletionPolicy).toBe('Retain');
  expect(JSON.stringify(key.Properties.KeyPolicy)).toContain('cloudwatch.amazonaws.com');
  expect(JSON.stringify(key.Properties.KeyPolicy)).not.toContain('events.amazonaws.com');
  const publisher = Object.entries(t.findResources('AWS::IAM::Role')).find(([id]) => id.startsWith('StoppedEventPublisher'))![1] as any;
  expect(JSON.stringify(publisher.Properties.AssumeRolePolicyDocument)).toContain('aws:SourceArn');
  for (const [id, policy] of Object.entries(t.findResources('AWS::IAM::Policy'))) {
    if (/^(TaskRole|ProxyTaskRole)/.test(id)) expect(JSON.stringify(policy)).not.toContain('kms:');
  }
  t.resourceCountIs('AWS::CloudWatch::Alarm', 9);
  for (const alarm of Object.values(t.findResources('AWS::CloudWatch::Alarm')) as any[]) {
    expect(alarm.Properties.AlarmActions).toHaveLength(1);
  }
});

test('AWS endpoints cannot provide arbitrary bucket, secret or database access', () => {
  const t = managed();
  const endpoints = t.findResources('AWS::EC2::VPCEndpoint');
  for (const endpoint of Object.values(endpoints) as any[]) {
    expect(endpoint.Properties.PolicyDocument).toBeDefined();
    const statements = endpoint.Properties.PolicyDocument.Statement;
    for (const statement of statements) {
      if (JSON.stringify(statement.Principal) === '{"AWS":"*"}') {
        if (statement.Action === 's3:GetObject') {
          expect(statement.Resource).toContain('prod-us-east-2-starport-layer-bucket/');
        } else {
          expect(JSON.stringify(statement.Condition)).toContain('aws:PrincipalArn');
          expect(JSON.stringify(statement.Condition)).toContain('TaskRole');
        }
      }
    }
  }
  const secret = Object.entries(endpoints).find(([id]) => id.startsWith('VpcSecrets'))![1] as any;
  expect(JSON.stringify(secret.Properties.PolicyDocument)).not.toContain('ProxyExecutionRole');
  expect(JSON.stringify(secret.Properties.PolicyDocument)).toContain(props.secretArn);
});
