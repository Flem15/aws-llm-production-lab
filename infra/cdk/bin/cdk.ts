#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { EcrStack } from '../lib/ecr-stack';

const app = new cdk.App();

new EcrStack(app, 'LlmParityDevEcrStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-2',
  },
  synthesizer: new cdk.DefaultStackSynthesizer({
    qualifier: 'prod01',
  }),
});
