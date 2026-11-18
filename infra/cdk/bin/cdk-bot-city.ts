#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { BotCityStack } from '../lib/bot-city-stack';
import { applyStandardTags } from '../lib/standard-tags';
const app = new cdk.App();
const enabled = app.node.tryGetContext('botCityManagedEgress');
if (enabled !== undefined && !['true', 'false', true, false].includes(enabled)) {
  throw new Error('botCityManagedEgress must be true or false');
}
if (app.node.tryGetContext('botCityProxyAddress') || app.node.tryGetContext('botCityProxySecurityGroupId')) {
  throw new Error('External proxy overrides are no longer supported');
}
new BotCityStack(app, 'BotCityDevStack', {
  env: { account: '655469962246', region: 'us-east-2' },
  synthesizer: new cdk.DefaultStackSynthesizer({ qualifier: 'prod01' }),
  imageDigest: app.node.tryGetContext('botCityImageDigest') || '',
  secretArn: app.node.tryGetContext('botCitySecretArn') || '',
  paperAccountId: app.node.tryGetContext('botCityPaperAccountId') || '',
  egress: enabled === true || enabled === 'true' ? {
    reviewId: app.node.tryGetContext('botCityEgressReviewId') || '',
    proxyImageDigest: app.node.tryGetContext('botCityProxyImageDigest') || '',
  } : undefined,
});
applyStandardTags(app, 'dev');
