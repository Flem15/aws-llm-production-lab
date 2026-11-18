import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { isDeepStrictEqual } from 'node:util';
import './test-support';
import { matchesPartial } from './test-support';
import {
  resolveEnvironmentConfig,
} from '../lib/environment-config';

const VALID_SHA = 'a'.repeat(40);
const PROD_ACCOUNT = '123456789012';

describe('Day 14 environment configuration', () => {
  test('preserves development defaults', () => {
    const config = resolveEnvironmentConfig({});

    assert.strictEqual(config.name, 'dev');
    assert.strictEqual(config.account, '655469962246');
    assert.strictEqual(config.region, 'us-east-2');
    assert.strictEqual(config.stackPrefix, 'LlmParityDev');
    assert.strictEqual(config.resourcePrefix, 'llm-parity');
    assert.strictEqual(config.githubEnvironment, 'dev');
    assert.strictEqual(config.monthlyBudgetLimit, 100);
    assert.strictEqual(config.imageTag, 'v1');
  });

  test('rejects unsupported environments', () => {
    assert.throws(() =>
      resolveEnvironmentConfig({
        environment: 'staging',
      }));
  });

  test('rejects an unexpected development account', () => {
    assert.throws(() =>
      resolveEnvironmentConfig({
        environment: 'dev',
        devAccount: PROD_ACCOUNT,
      }));
  });

  test('rejects production without an account', () => {
    assert.throws(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        imageTag: VALID_SHA,
        monthlyBudgetLimit: 250,
      }));
  });

  test('rejects production in the development account', () => {
    assert.throws(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        prodAccount: '655469962246',
        imageTag: VALID_SHA,
        monthlyBudgetLimit: 250,
      }));
  });

  test('rejects mutable production image tags', () => {
    assert.throws(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        prodAccount: PROD_ACCOUNT,
        imageTag: 'v1',
        monthlyBudgetLimit: 250,
      }));
  });

  test('requires an explicit production budget', () => {
    assert.throws(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        prodAccount: PROD_ACCOUNT,
        imageTag: VALID_SHA,
      }));
  });

  test('rejects production deployment enablement', () => {
    assert.throws(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        prodAccount: PROD_ACCOUNT,
        imageTag: VALID_SHA,
        monthlyBudgetLimit: 250,
        enableProductionDeployment: true,
      }));
  });

  test('resolves production with deployment disabled', () => {
    const config = resolveEnvironmentConfig({
      environment: 'prod',
      prodAccount: PROD_ACCOUNT,
      imageTag: VALID_SHA,
      monthlyBudgetLimit: 250,
    });

    assert.strictEqual(config.name, 'prod');
    assert.strictEqual(config.account, PROD_ACCOUNT);
    assert.strictEqual(config.stackPrefix, 'LlmParityProd');
    assert.strictEqual(config.resourcePrefix, 'llm-parity-prod');
    assert.strictEqual(config.githubEnvironment, 'production');
    assert.strictEqual(config.deploymentEnabled, false);
  });
});
