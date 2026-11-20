import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { BotCityStack } from '../lib/bot-city-stack';
const props = { env: { account: '655469962246', region: 'us-east-2' },
  imageDigest: `sha256:${'a'.repeat(64)}`,
  secretArn: 'arn:aws:secretsmanager:us-east-2:655469962246:secret:bot-city/dev/alpaca-paper-ABC123',
  paperAccountId: 'd8513544-ac5a-4731-8484-d4b087a9d259',
  egress: { reviewId: 'pending-review', proxyImageDigest: `sha256:${'b'.repeat(64)}` } };
function template() { return Template.fromStack(new BotCityStack(new cdk.App(), 'BotCityDevStack', props)); }
test('activation references existing repositories with exact immutable image digests and no ownership transfer', () => {
  const t = template(); t.resourceCountIs('AWS::ECR::Repository', 0);
  const tasks = Object.values(t.findResources('AWS::ECS::TaskDefinition')) as any[];
  const images = tasks.map(t => JSON.stringify(t.Properties.ContainerDefinitions[0].Image));
  assert.ok(images.some(s => s.includes(`bot-city-dev@${props.imageDigest}`)));
  assert.ok(images.some(s => s.includes(`bot-city-proxy-dev@${props.egress.proxyImageDigest}`)));
  const all = JSON.stringify(t.toJSON());
  assert.ok(!all.includes('ecr:PutImage')); assert.ok(!all.includes('ecr:CreateRepository'));
});
test('notification recipient is required, private in parameters, and encrypted topic subscription is defined', () => {
  const t = template(); t.resourceCountIs('AWS::SNS::Subscription', 1);
  const p = t.toJSON().Parameters.AlarmEmail;
  assert.equal(p.NoEcho, true); assert.equal(p.Default, undefined);
  assert.ok(new RegExp(p.AllowedPattern).test('operator@example.com'));
  assert.ok(!new RegExp(p.AllowedPattern).test('not an email'));
  const subscription = Object.values(t.findResources('AWS::SNS::Subscription'))[0] as any;
  assert.equal(subscription.Properties.Protocol, 'email');
  assert.deepEqual(subscription.Properties.Endpoint, { Ref: 'AlarmEmail' });
  const topic = Object.values(t.findResources('AWS::SNS::Topic'))[0] as any;
  assert.ok(topic.Properties.KmsMasterKeyId);
});
test('activation preserves zero tasks, paper mode, disabled trading and exact account/secret grants', () => {
  const t = template();
  for (const svc of Object.values(t.findResources('AWS::ECS::Service')) as any[]) {
    assert.equal(svc.Properties.DesiredCount, 0);
    assert.equal(svc.Properties.NetworkConfiguration.AwsvpcConfiguration.AssignPublicIp, 'DISABLED');
  }
  const tasks = Object.values(t.findResources('AWS::ECS::TaskDefinition')) as any[];
  const bot = tasks.flatMap(t => t.Properties.ContainerDefinitions).find(c => c.Name === 'bot-city');
  assert.ok(bot.Environment.some((x: any) => x.Name === 'BOT_CITY_TRADING_ENABLED' && x.Value === 'false'));
  assert.ok(bot.Environment.some((x: any) => x.Name === 'BOT_CITY_MODE' && x.Value === 'paper-alpaca'));
  assert.ok(bot.Secrets.every((x: any) => JSON.stringify(x.ValueFrom).includes(props.secretArn)));
  assert.ok(JSON.stringify(t.toJSON()).includes(props.paperAccountId));
});

test('all workload and provider roles have bounded permissions and stable scoped names', () => {
  const t = template();
  const roles = Object.values(t.findResources('AWS::IAM::Role')) as any[];
  assert.equal(roles.length, 6);
  for (const role of roles) {
    assert.match(role.Properties.RoleName, /^bot-city-dev-/);
    assert.ok(role.Properties.RoleName.length <= 64);
    assert.equal(role.Properties.PermissionsBoundary,
      'arn:aws:iam::655469962246:policy/bot-city-dev-workload-boundary');
    assert.ok(role.Properties.Tags.some((t: any) => t.Key === 'BotCityVpcId'));
  }
});
