import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ecr from 'aws-cdk-lib/aws-ecr';

export interface EcrStackProps extends cdk.StackProps {
  readonly environment?: 'dev' | 'prod';
}

export class EcrStack extends cdk.Stack {
  public readonly repository: ecr.Repository;

  constructor(scope: Construct, id: string, props?: EcrStackProps) {
    super(scope, id, props);

    this.repository = new ecr.Repository(this, 'DemoInferenceRepository', {
      repositoryName:
        props?.environment === 'prod'
          ? 'demo-inference-api-prod'
          : 'demo-inference-api',
        imageTagMutability: ecr.TagMutability.IMMUTABLE,
      imageScanOnPush: true,
      removalPolicy:
        props?.environment === 'prod'
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: props?.environment !== 'prod',
    });

    new cdk.CfnOutput(this, 'RepositoryName', {
      value: this.repository.repositoryName,
    });

    new cdk.CfnOutput(this, 'RepositoryUri', {
      value: this.repository.repositoryUri,
    });
  }
}
