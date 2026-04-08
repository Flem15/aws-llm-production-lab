#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { DefaultStackSynthesizer } from 'aws-cdk-lib';
import { EcrStack } from '../lib/ecr-stack';
import { NetworkStack } from '../lib/network-stack';

const app = new cdk.App();

const commonProps: cdk.StackProps = {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-2',
  },
  synthesizer: new DefaultStackSynthesizer({
    qualifier: 'prod01',
  }),
};

new EcrStack(app, 'LlmParityDevEcrStack', commonProps);

new NetworkStack(app, 'LlmParityDevNetworkStack', commonProps);
