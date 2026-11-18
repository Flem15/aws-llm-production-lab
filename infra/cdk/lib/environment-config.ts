export type DeploymentEnvironment = 'dev' | 'prod';

export interface EnvironmentConfig {
  readonly name: DeploymentEnvironment;
  readonly account: string | undefined;
  readonly region: string;
  readonly stackPrefix: string;
  readonly resourcePrefix: string;
  readonly githubEnvironment: string;
  readonly monthlyBudgetLimit: number;
  readonly imageTag: string;
  readonly deploymentEnabled: boolean;
}

export interface EnvironmentConfigInput {
  readonly environment?: string;
  readonly devAccount?: string;
  readonly prodAccount?: string;
  readonly region?: string;
  readonly imageTag?: string;
  readonly monthlyBudgetLimit?: string | number;
  readonly enableProductionDeployment?: string | boolean;
}

const DEV_ACCOUNT = '655469962246';
const ACCOUNT_ID_PATTERN = /^[0-9]{12}$/;
const IMMUTABLE_SHA_PATTERN = /^[0-9a-f]{40}$/;

export function resolveEnvironmentConfig(
  input: EnvironmentConfigInput,
): EnvironmentConfig {
  const environment = input.environment ?? 'dev';

  if (environment !== 'dev' && environment !== 'prod') {
    throw new Error(
      'environment must be explicitly set to dev or prod',
    );
  }

  const region = input.region ?? 'us-east-2';

  if (region !== 'us-east-2') {
    throw new Error(
      'Only us-east-2 is supported by this lab configuration',
    );
  }

  const imageTag = input.imageTag ?? 'v1';

  if (
    imageTag !== 'v1' &&
    !IMMUTABLE_SHA_PATTERN.test(imageTag)
  ) {
    throw new Error(
      'imageTag must be v1 or a lowercase 40-character Git SHA',
    );
  }

  const monthlyBudgetLimit = Number(
    input.monthlyBudgetLimit ?? 100,
  );

  if (
    !Number.isFinite(monthlyBudgetLimit) ||
    monthlyBudgetLimit <= 0
  ) {
    throw new Error(
      'monthlyBudgetLimit must be a positive number',
    );
  }

  if (environment === 'dev') {
    if (
      input.devAccount !== undefined &&
      input.devAccount !== DEV_ACCOUNT
    ) {
      throw new Error(
        'Development account does not match the approved account',
      );
    }

    return {
      name: 'dev',
      account: DEV_ACCOUNT,
      region,
      stackPrefix: 'LlmParityDev',
      resourcePrefix: 'llm-parity',
      githubEnvironment: 'dev',
      monthlyBudgetLimit,
      imageTag,
      deploymentEnabled: true,
    };
  }

  if (
    !input.prodAccount ||
    !ACCOUNT_ID_PATTERN.test(input.prodAccount)
  ) {
    throw new Error(
      'Production requires an explicit 12-digit AWS account ID',
    );
  }

  if (input.prodAccount === DEV_ACCOUNT) {
    throw new Error(
      'Production must use a separate AWS account',
    );
  }

  if (!IMMUTABLE_SHA_PATTERN.test(imageTag)) {
    throw new Error(
      'Production requires an immutable 40-character Git SHA',
    );
  }

  if (input.monthlyBudgetLimit === undefined) {
    throw new Error(
      'Production requires an explicit monthlyBudgetLimit',
    );
  }

  if (
    input.enableProductionDeployment === true ||
    input.enableProductionDeployment === 'true'
  ) {
    throw new Error(
      'Production deployment is locked until Stage C security controls are verified',
    );
  }

  return {
    name: 'prod',
    account: input.prodAccount,
    region,
    stackPrefix: 'LlmParityProd',
    resourcePrefix: 'llm-parity-prod',
    githubEnvironment: 'production',
    monthlyBudgetLimit,
    imageTag,
    deploymentEnabled: false,
  };
}
