const fs = require('node:fs');
const path = require('node:path');

const cdkOutDirectory = path.resolve(
  process.cwd(),
  'cdk.out',
);

const requiredTags = {
  Project:
    'aws-llm-production-parity-lab',
  Environment:
    'dev',
  CostCenter:
    'llm-parity-lab',
  ManagedBy:
    'AWS-CDK',
  Repository:
    'Flem15/aws-llm-production-lab',
};

const financiallyRelevantTypes = new Set([
  'AWS::ApiGatewayV2::Api',
  'AWS::ApiGatewayV2::VpcLink',
  'AWS::EC2::EIP',
  'AWS::EC2::NatGateway',
  'AWS::EC2::SecurityGroup',
  'AWS::EC2::Subnet',
  'AWS::EC2::VPC',
  'AWS::ECR::Repository',
  'AWS::ECS::Cluster',
  'AWS::ECS::Service',
  'AWS::ECS::TaskDefinition',
  'AWS::ElasticLoadBalancingV2::LoadBalancer',
  'AWS::ElasticLoadBalancingV2::TargetGroup',
  'AWS::Logs::LogGroup',
  'AWS::SNS::Topic',
]);

if (!fs.existsSync(cdkOutDirectory)) {
  console.error(
    'ERROR: cdk.out does not exist. Run CDK synth first.',
  );
  process.exit(1);
}

const templateFiles = fs
  .readdirSync(cdkOutDirectory)
  .filter((file) =>
    file.endsWith('.template.json'),
  )
  .sort();

if (templateFiles.length === 0) {
  console.error(
    'ERROR: no synthesized CloudFormation templates were found.',
  );
  process.exit(1);
}

let checkedResources = 0;
let relevantResources = 0;
const failures = [];

function tagsToMap(tags) {
  if (Array.isArray(tags)) {
    return new Map(
      tags
        .filter(
          (tag) =>
            tag &&
            typeof tag === 'object' &&
            typeof tag.Key === 'string',
        )
        .map((tag) => [
          tag.Key,
          tag.Value,
        ]),
    );
  }

  if (
    tags &&
    typeof tags === 'object'
  ) {
    return new Map(
      Object.entries(tags),
    );
  }

  return new Map();
}

for (const templateFile of templateFiles) {
  const absolutePath = path.join(
    cdkOutDirectory,
    templateFile,
  );

  const template = JSON.parse(
    fs.readFileSync(
      absolutePath,
      'utf8',
    ),
  );

  const resources =
    template.Resources ?? {};

  for (
    const [logicalId, resource] of
    Object.entries(resources)
  ) {
    const resourceType =
      resource.Type;

    if (
      !financiallyRelevantTypes.has(
        resourceType,
      )
    ) {
      continue;
    }

    relevantResources += 1;

    const tags =
      resource.Properties?.Tags;

    const tagMap =
      tagsToMap(tags);

    for (
      const [key, expectedValue] of
      Object.entries(requiredTags)
    ) {
      if (!tagMap.has(key)) {
        failures.push(
          `${templateFile} :: ${resourceType} :: ${logicalId} :: missing ${key}`,
        );

        continue;
      }

      const actualValue =
        tagMap.get(key);

      if (
        actualValue !==
        expectedValue
      ) {
        failures.push(
          `${templateFile} :: ${resourceType} :: ${logicalId} :: ${key} expected "${expectedValue}" but received "${actualValue}"`,
        );
      }
    }

    checkedResources += 1;
  }
}

if (relevantResources === 0) {
  console.error(
    'ERROR: no financially relevant resources were found in synthesized templates.',
  );
  process.exit(1);
}

if (failures.length > 0) {
  console.error(
    'ERROR: required tag compliance failed:',
  );

  for (const failure of failures) {
    console.error(
      `  - ${failure}`,
    );
  }

  process.exit(1);
}

console.log(
  `PASS: ${checkedResources} financially relevant synthesized resources contain every required standard tag`,
);
