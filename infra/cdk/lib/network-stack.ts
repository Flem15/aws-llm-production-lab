import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as servicediscovery from 'aws-cdk-lib/aws-servicediscovery';

export class NetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly namespace: servicediscovery.PrivateDnsNamespace;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, 'LlmParityVpc', {
      vpcName: 'llm-parity-vpc',
      ipAddresses: ec2.IpAddresses.cidr('10.0.0.0/16'),
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        {
          name: 'private-egress',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 24,
        },
      ],
    });

    this.namespace = new servicediscovery.PrivateDnsNamespace(this, 'LlmInternalNamespace', {
      name: 'llm.internal',
      vpc: this.vpc,
      description: 'Private namespace for ECS service discovery',
    });

    new cdk.CfnOutput(this, 'VpcId', {
      value: this.vpc.vpcId,
    });

    new cdk.CfnOutput(this, 'PrivateNamespaceName', {
      value: this.namespace.namespaceName,
    });
  }
}
