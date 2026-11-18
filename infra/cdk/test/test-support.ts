import assert from 'node:assert/strict';
import { after } from 'node:test';
import { CloudAssembly } from 'aws-cdk-lib/cx-api';
// Preserve the cleanup previously supplied by CDK's Jest integration.
after(() => CloudAssembly.cleanupTemporaryDirectories());
// Used only where the original assertion explicitly requested objectContaining.
export function matchesPartial(actual: unknown, expected: unknown): boolean {
  try { assert.partialDeepStrictEqual(actual, expected); return true; }
  catch (error) { if (error instanceof assert.AssertionError) return false; throw error; }
}
