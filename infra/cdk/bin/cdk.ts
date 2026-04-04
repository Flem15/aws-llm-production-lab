#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { EcrStack } from '../lib/ecr-stack';

const app = new cdk.App();

new EcrStack(app, 'LlmParityDevEcrStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT ?? '655469962246',
    region: 'us-east-2',
  },
});
