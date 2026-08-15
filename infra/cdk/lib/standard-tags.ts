import { Tags } from 'aws-cdk-lib';
import { IConstruct } from 'constructs';

export const STANDARD_TAGS = Object.freeze({
  Project: 'aws-llm-production-parity-lab',
  Environment: 'dev',
  CostCenter: 'llm-parity-lab',
  ManagedBy: 'AWS-CDK',
  Repository: 'Flem15/aws-llm-production-lab',
});

export type StandardTagKey =
  keyof typeof STANDARD_TAGS;

export const COST_ALLOCATION_TAG_KEYS = Object.freeze([
  'Project',
  'Environment',
  'CostCenter',
] as const);

export function applyStandardTags(
  scope: IConstruct,
): void {
  for (
    const [key, value] of
    Object.entries(STANDARD_TAGS)
  ) {
    Tags.of(scope).add(key, value);
  }
}
