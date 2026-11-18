import {
  resolveEnvironmentConfig,
} from '../lib/environment-config';

const VALID_SHA = 'a'.repeat(40);
const PROD_ACCOUNT = '123456789012';

describe('Day 14 environment configuration', () => {
  test('preserves development defaults', () => {
    const config = resolveEnvironmentConfig({});

    expect(config.name).toBe('dev');
    expect(config.account).toBe('655469962246');
    expect(config.region).toBe('us-east-2');
    expect(config.stackPrefix).toBe('LlmParityDev');
    expect(config.resourcePrefix).toBe('llm-parity');
    expect(config.githubEnvironment).toBe('dev');
    expect(config.monthlyBudgetLimit).toBe(100);
    expect(config.imageTag).toBe('v1');
  });

  test('rejects unsupported environments', () => {
    expect(() =>
      resolveEnvironmentConfig({
        environment: 'staging',
      }),
    ).toThrow();
  });

  test('rejects an unexpected development account', () => {
    expect(() =>
      resolveEnvironmentConfig({
        environment: 'dev',
        devAccount: PROD_ACCOUNT,
      }),
    ).toThrow();
  });

  test('rejects production without an account', () => {
    expect(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        imageTag: VALID_SHA,
        monthlyBudgetLimit: 250,
      }),
    ).toThrow();
  });

  test('rejects production in the development account', () => {
    expect(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        prodAccount: '655469962246',
        imageTag: VALID_SHA,
        monthlyBudgetLimit: 250,
      }),
    ).toThrow();
  });

  test('rejects mutable production image tags', () => {
    expect(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        prodAccount: PROD_ACCOUNT,
        imageTag: 'v1',
        monthlyBudgetLimit: 250,
      }),
    ).toThrow();
  });

  test('requires an explicit production budget', () => {
    expect(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        prodAccount: PROD_ACCOUNT,
        imageTag: VALID_SHA,
      }),
    ).toThrow();
  });

  test('rejects production deployment enablement', () => {
    expect(() =>
      resolveEnvironmentConfig({
        environment: 'prod',
        prodAccount: PROD_ACCOUNT,
        imageTag: VALID_SHA,
        monthlyBudgetLimit: 250,
        enableProductionDeployment: true,
      }),
    ).toThrow();
  });

  test('resolves production with deployment disabled', () => {
    const config = resolveEnvironmentConfig({
      environment: 'prod',
      prodAccount: PROD_ACCOUNT,
      imageTag: VALID_SHA,
      monthlyBudgetLimit: 250,
    });

    expect(config.name).toBe('prod');
    expect(config.account).toBe(PROD_ACCOUNT);
    expect(config.stackPrefix).toBe('LlmParityProd');
    expect(config.resourcePrefix).toBe('llm-parity-prod');
    expect(config.githubEnvironment).toBe('production');
    expect(config.deploymentEnabled).toBe(false);
  });
});
