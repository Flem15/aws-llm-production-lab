#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { EcrStack } from '../lib/ecr-stack';
import { NetworkStack } from '../lib/network-stack';

const app = new cdk.App();

new EcrStack(app, 'LlmParityDevEcrStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-2',
  },
});

new NetworkStack(app, 'LlmParityDevNetworkStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-2',
  },
});
