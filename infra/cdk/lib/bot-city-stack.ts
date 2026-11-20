import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secrets from 'aws-cdk-lib/aws-secretsmanager';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';

export interface BotCityProps extends cdk.StackProps {
  imageDigest: string;
  secretArn: string;
  paperAccountId: string;
  // Only infrastructure is enabled: proxy desiredCount and trading stay zero/false.
  egress?: { reviewId: string; proxyImageDigest: string };

}

export class BotCityStack extends cdk.Stack {
  override get availabilityZones(): string[] {
    return [0, 1].map(index => cdk.Fn.select(index, cdk.Fn.getAzs()));
  }

  constructor(scope: Construct, id: string, props: BotCityProps) {
    super(scope, id, props);
    cdk.Tags.of(this).add('BotCityDeployment', 'BotCityDevStack');
    if (props.env?.account !== '655469962246' || props.env?.region !== 'us-east-2') {
      throw new Error('Bot City is restricted to the approved development account and region');
    }
    if (!/^sha256:[a-f0-9]{64}$/.test(props.imageDigest)) throw new Error('Immutable image digest required');
    if (!/^arn:aws:secretsmanager:us-east-2:655469962246:secret:[\w/+=.@-]+-[A-Za-z0-9]{6}$/.test(props.secretArn)) {
      throw new Error('Complete development Secrets Manager ARN required');
    }
    if (!/^[A-Za-z0-9-]{1,64}$/.test(props.paperAccountId)) throw new Error('Exact paper account ID required');
    if (props.egress && (!/^[A-Za-z0-9-]{3,80}$/.test(props.egress.reviewId) ||
        !/^sha256:[a-f0-9]{64}$/.test(props.egress.proxyImageDigest))) {
      throw new Error('Review ID and immutable proxy digest required');
    }
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2, natGateways: 0, createInternetGateway: false, restrictDefaultSecurityGroup: true,
      subnetConfiguration: [{ name: 'BotCityIsolated', subnetType: ec2.SubnetType.PRIVATE_ISOLATED }],
    });
    const taskSg = new ec2.SecurityGroup(this, 'TaskSecurityGroup', { vpc, allowAllOutbound: false });
    const endpointSg = new ec2.SecurityGroup(this, 'EndpointSecurityGroup', { vpc, allowAllOutbound: false });
    endpointSg.addIngressRule(taskSg, ec2.Port.tcp(443));
    taskSg.addEgressRule(endpointSg, ec2.Port.tcp(443));
    const interfaces = new Map<string, ec2.InterfaceVpcEndpoint>();
    const gateways = new Map<string, ec2.GatewayVpcEndpoint>();
    for (const [name, service] of Object.entries({ Ecr: ec2.InterfaceVpcEndpointAwsService.ECR,
      EcrDocker: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER, Logs: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
      Secrets: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER })) {
      interfaces.set(name, vpc.addInterfaceEndpoint(name, { service, securityGroups: [endpointSg], open: false, privateDnsEnabled: true }));
    }
    for (const [name, service] of Object.entries({ S3: ec2.GatewayVpcEndpointAwsService.S3,
      DynamoDb: ec2.GatewayVpcEndpointAwsService.DYNAMODB })) {
      gateways.set(name, vpc.addGatewayEndpoint(name, { service }));
      const prefix = new cdk.CfnParameter(this, `${name}PrefixListId`, {
        type: 'String', allowedPattern: 'pl-[a-f0-9]+', description: `Verified us-east-2 ${name} managed prefix list ID`,
      });
      taskSg.addEgressRule(ec2.Peer.prefixList(prefix.valueAsString), ec2.Port.tcp(443));
    }
    const table = (name: string, partition: string, sort?: string) => new dynamodb.Table(this, name, {
      partitionKey: { name: partition, type: dynamodb.AttributeType.STRING },
      sortKey: sort ? { name: sort, type: dynamodb.AttributeType.STRING } : undefined,
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      deletionProtection: true, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const lease = table('Leases', 'lease_key');
    const state = table('State', 'account_id');
    const history = table('History', 'account_id', 'record_key');
    const ecsPrincipal = () => new iam.ServicePrincipal('ecs-tasks.amazonaws.com', { conditions: {
      StringEquals: { 'aws:SourceAccount': this.account },
      ArnLike: { 'aws:SourceArn': `arn:aws:ecs:${this.region}:${this.account}:*` },
    } });
    const taskRole = new iam.Role(this, 'TaskRole', { assumedBy: ecsPrincipal() });
    const executionRole = new iam.Role(this, 'ExecutionRole', { assumedBy: ecsPrincipal() });
    const policy = (resource: dynamodb.Table, permissions: string[], key: string) => taskRole.addToPolicy(new iam.PolicyStatement({
      actions: permissions.map(action => `dynamodb:${action}`), resources: [resource.tableArn],
      conditions: { 'ForAllValues:StringEquals': { 'dynamodb:LeadingKeys': [key] } },
    }));
    policy(lease, ['PutItem', 'UpdateItem', 'DeleteItem', 'ConditionCheckItem'], 'alpaca-paper');
    policy(state, ['GetItem', 'PutItem'], props.paperAccountId);
    policy(history, ['GetItem', 'Query', 'PutItem'], props.paperAccountId);
    // Owned by LlmParityDevEcrStack; reference only, never duplicate or transfer ownership.
    const repository = ecr.Repository.fromRepositoryName(this, 'Repository', 'bot-city-dev');
    const logGroup = new logs.LogGroup(this, 'Logs', {
      retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const endpointExecutors: iam.IRole[] = [executionRole];
    const endpointRepos: string[] = [repository.repositoryArn];
    const endpointLogs: string[] = [logGroup.logGroupArn];
    const cluster = new ecs.Cluster(this, 'Cluster', { vpc });
    const task = new ecs.FargateTaskDefinition(this, 'Task', {
      cpu: 512, memoryLimitMiB: 2048, taskRole, executionRole,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.X86_64, operatingSystemFamily: ecs.OperatingSystemFamily.LINUX },
    });
    const secret = secrets.Secret.fromSecretCompleteArn(this, 'AlpacaSecret', props.secretArn);
    task.addContainer('bot-city', {
      image: ecs.ContainerImage.fromEcrRepository(repository, props.imageDigest),
      logging: ecs.LogDrivers.awsLogs({ logGroup, streamPrefix: 'bot-city' }),
      user: 'botcity', essential: true, stopTimeout: cdk.Duration.seconds(120),
      environment: {
        BOT_CITY_MODE: 'paper-alpaca', BOT_CITY_TRADING_ENABLED: 'false',
        BOT_CITY_LEASE_MODE: 'dynamodb', BOT_CITY_LEASE_TABLE: lease.tableName,
        BOT_CITY_STATE_TABLE: state.tableName, BOT_CITY_HISTORY_TABLE: history.tableName,
        AWS_REGION: this.region,
        BOT_CITY_EGRESS_MODE: props.egress ? 'proxy' : 'disabled',
        ...(props.egress ? { BOT_CITY_PROXY_URL: 'http://alpaca-egress.bot-city.internal:3128' } : {}),
      },
      secrets: { ALPACA_API_KEY: ecs.Secret.fromSecretsManager(secret, 'ALPACA_API_KEY'),
        ALPACA_SECRET_KEY: ecs.Secret.fromSecretsManager(secret, 'ALPACA_SECRET_KEY') },
      command: ['python', 'run_bots.py', '--no-dashboard'],
    });
    const alarmKey = new kms.Key(this, 'AlarmKey', {
      enableKeyRotation: true, removalPolicy: cdk.RemovalPolicy.RETAIN,
      description: 'Bot City alarm topic encryption only',
    });
    alarmKey.addToResourcePolicy(new iam.PolicyStatement({
      principals: [new iam.ServicePrincipal('cloudwatch.amazonaws.com')],
      actions: ['kms:Decrypt', 'kms:GenerateDataKey*'], resources: ['*'],
      conditions: { StringEquals: { 'aws:SourceAccount': this.account },
        ArnLike: { 'aws:SourceArn': `arn:aws:cloudwatch:${this.region}:${this.account}:alarm:*` } },
    }));
    const topic = new sns.Topic(this, 'Alarms', { masterKey: alarmKey });
    const alarmEmail = new cdk.CfnParameter(this, 'AlarmEmail', {
      type: 'String', noEcho: true,
      allowedPattern: '^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$',
      description: 'Approved development incident-alert recipient; confirmation required',
    });
    topic.addSubscription(new subscriptions.EmailSubscription(alarmEmail.valueAsString));
    topic.addToResourcePolicy(new iam.PolicyStatement({
      principals: [new iam.ServicePrincipal('cloudwatch.amazonaws.com')],
      actions: ['sns:Publish'], resources: [topic.topicArn],
      conditions: { StringEquals: { 'aws:SourceAccount': this.account },
        ArnLike: { 'aws:SourceArn': `arn:aws:cloudwatch:${this.region}:${this.account}:alarm:*` } },
    }));
    const failure = new logs.MetricFilter(this, 'SafetyFailure', {
      logGroup, filterPattern: logs.FilterPattern.anyTerm('SAFETY HALT', 'lease renewal failed', 'lease lost'),
      metricNamespace: 'BotCity', metricName: 'SafetyFailure', metricValue: '1',
    });
    const alarm = (name: string, metric: cloudwatch.IMetric) => {
      const a = new cloudwatch.Alarm(this, name, { metric, threshold: 1, evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING });
      a.addAlarmAction(new actions.SnsAction(topic));
    };
    alarm('SafetyAlarm', failure.metric({ statistic: 'Sum', period: cdk.Duration.minutes(1) }));
    for (const t of [lease, state, history]) {
      for (const metricName of ['ReadThrottleEvents', 'WriteThrottleEvents']) {
        alarm(`${t.node.id}${metricName}`, new cloudwatch.Metric({ namespace: 'AWS/DynamoDB', metricName,
          dimensionsMap: { TableName: t.tableName }, statistic: 'Sum', period: cdk.Duration.minutes(1) }));
      }
    }
    if (props.egress) {
      // Preserve the original isolated subnet CIDRs/route tables. A secondary
      // CIDR contains the NAT and proxy, never a default route in task subnets.
      const cidr = new ec2.CfnVPCCidrBlock(this, 'EgressCidr', { vpcId: vpc.vpcId, cidrBlock: '10.1.0.0/16' });
      const publicSubnet = new ec2.PublicSubnet(this, 'EgressPublicSubnet', {
        vpcId: vpc.vpcId, availabilityZone: this.availabilityZones[0], cidrBlock: '10.1.0.0/24', mapPublicIpOnLaunch: false,
      });
      const proxySubnet = new ec2.PrivateSubnet(this, 'EgressProxySubnet', {
        vpcId: vpc.vpcId, availabilityZone: this.availabilityZones[0], cidrBlock: '10.1.1.0/24', mapPublicIpOnLaunch: false,
      });
      publicSubnet.node.addDependency(cidr);
      proxySubnet.node.addDependency(cidr);
      const igw = new ec2.CfnInternetGateway(this, 'EgressInternetGateway');
      const attachment = new ec2.CfnVPCGatewayAttachment(this, 'EgressGatewayAttachment', {
        vpcId: vpc.vpcId, internetGatewayId: igw.ref,
      });
      publicSubnet.addDefaultInternetRoute(igw.ref, attachment);
      const eip = new ec2.CfnEIP(this, 'EgressNatIp', { domain: 'vpc' });
      const nat = new ec2.CfnNatGateway(this, 'EgressNat', {
        subnetId: publicSubnet.subnetId, allocationId: eip.attrAllocationId, connectivityType: 'public',
      });
      nat.addDependency(attachment);
      proxySubnet.addDefaultNatRoute(nat.ref);
      const proxySg = new ec2.SecurityGroup(this, 'ProxySecurityGroup', { vpc, allowAllOutbound: false });
      proxySg.addIngressRule(taskSg, ec2.Port.tcp(3128));
      taskSg.addEgressRule(proxySg, ec2.Port.tcp(3128), `Approved review ${props.egress.reviewId}`);
      // Only the proxy has Internet TCP 443. Dynamic upstream IP restrictions
      // are enforced by the proxy; the isolated task has no Internet route.
      proxySg.addEgressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(443));
      endpointSg.addIngressRule(proxySg, ec2.Port.tcp(443));
      const proxyRepo = ecr.Repository.fromRepositoryName(this, 'ProxyRepository', 'bot-city-proxy-dev');
      const proxyLogs = new logs.LogGroup(this, 'ProxyLogs', {
        retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.RETAIN,
      });
      const proxyRole = new iam.Role(this, 'ProxyTaskRole', { assumedBy: ecsPrincipal() });
      const proxyExecution = new iam.Role(this, 'ProxyExecutionRole', { assumedBy: ecsPrincipal() });
      endpointExecutors.push(proxyExecution);
      endpointRepos.push(proxyRepo.repositoryArn);
      endpointLogs.push(proxyLogs.logGroupArn);
      const proxyTask = new ecs.FargateTaskDefinition(this, 'ProxyTask', {
        cpu: 256, memoryLimitMiB: 512, taskRole: proxyRole, executionRole: proxyExecution,
        runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.X86_64, operatingSystemFamily: ecs.OperatingSystemFamily.LINUX },
      });
      proxyTask.addContainer('alpaca-egress', {
        image: ecs.ContainerImage.fromEcrRepository(proxyRepo, props.egress.proxyImageDigest),
        logging: ecs.LogDrivers.awsLogs({ logGroup: proxyLogs, streamPrefix: 'egress' }),
        user: 'proxyworker', readonlyRootFilesystem: true, stopTimeout: cdk.Duration.seconds(30),
        portMappings: [{ containerPort: 3128 }],
        healthCheck: { command: ['CMD', 'python', '-c',
          "import socket; socket.create_connection(('127.0.0.1',3128),2).close()"],
          interval: cdk.Duration.seconds(30), timeout: cdk.Duration.seconds(5), retries: 3 },
      });
      cluster.addDefaultCloudMapNamespace({ name: 'bot-city.internal' });
      const proxyService = new ecs.FargateService(this, 'ProxyService', {
        cluster, taskDefinition: proxyTask, desiredCount: 0, assignPublicIp: false,
        vpcSubnets: { subnets: [proxySubnet] }, securityGroups: [proxySg],
        cloudMapOptions: { name: 'alpaca-egress' }, circuitBreaker: { rollback: true },
        enableExecuteCommand: false,
      });
      for (const name of ['proxy_denied', 'proxy_upstream_error']) {
        const metric = new logs.MetricFilter(this, name, {
          logGroup: proxyLogs, filterPattern: logs.FilterPattern.stringValue('$.event', '=', name),
          metricNamespace: 'BotCity/Egress', metricName: name, metricValue: '1',
        });
        alarm(name + 'Alarm', metric.metric({ statistic: 'Sum', period: cdk.Duration.minutes(1) }));
      }
      for (const [name, value] of Object.entries({ ProxyServiceName: proxyService.serviceName,
        ProxyTaskDefinitionArn: proxyTask.taskDefinitionArn, ProxyRepositoryUri: proxyRepo.repositoryUri,
        ProxyLogGroup: proxyLogs.logGroupName, ProxySubnetId: proxySubnet.subnetId,
        EgressReviewId: props.egress.reviewId })) {
        new cdk.CfnOutput(this, name, { value });
      }
    }
    // Endpoint paths are not a general AWS-service exfiltration escape hatch.
    gateways.get('S3')!.addToPolicy(new iam.PolicyStatement({
      principals: [new iam.AnyPrincipal()], actions: ['s3:GetObject'],
      resources: [`arn:aws:s3:::prod-${this.region}-starport-layer-bucket/*`],
    }));
    gateways.get('DynamoDb')!.addToPolicy(new iam.PolicyStatement({
      principals: [new iam.AnyPrincipal()],
      conditions: { ArnEquals: { 'aws:PrincipalArn': taskRole.roleArn } },
      actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem',
        'dynamodb:Query', 'dynamodb:ConditionCheckItem'],
      resources: [lease.tableArn, state.tableArn, history.tableArn],
    }));
    const executors = endpointExecutors.map(role => new iam.ArnPrincipal(role.roleArn));
    for (const name of ['Ecr', 'EcrDocker']) {
      interfaces.get(name)!.addToPolicy(new iam.PolicyStatement({
        principals: executors, actions: ['ecr:GetAuthorizationToken'], resources: ['*'],
      }));
      interfaces.get(name)!.addToPolicy(new iam.PolicyStatement({
        principals: executors, actions: ['ecr:BatchCheckLayerAvailability', 'ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer'],
        resources: endpointRepos,
      }));
    }
    interfaces.get('Logs')!.addToPolicy(new iam.PolicyStatement({
      principals: executors, actions: ['logs:CreateLogStream', 'logs:PutLogEvents'], resources: endpointLogs,
    }));
    interfaces.get('Secrets')!.addToPolicy(new iam.PolicyStatement({
      principals: [new iam.ArnPrincipal(executionRole.roleArn)],
      actions: ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'], resources: [props.secretArn],
    }));
    const stoppedRuleName = `${this.stackName}-task-stopped`;
    const stoppedPublisher = new iam.Role(this, 'StoppedEventPublisher', {
      assumedBy: new iam.ServicePrincipal('events.amazonaws.com', { conditions: {
        StringEquals: { 'aws:SourceAccount': this.account },
        ArnEquals: { 'aws:SourceArn': `arn:aws:events:${this.region}:${this.account}:rule/${stoppedRuleName}` },
      } }),
    });
    new events.Rule(this, 'TaskStopped', {
      ruleName: stoppedRuleName,
      eventPattern: { source: ['aws.ecs'], detailType: ['ECS Task State Change'],
        detail: { clusterArn: [cluster.clusterArn], lastStatus: ['STOPPED'] } },
      targets: [new targets.SnsTopic(topic, { role: stoppedPublisher })],
    });
    for (const [name, value] of Object.entries({ VpcId: vpc.vpcId,
      IsolatedSubnetIds: vpc.isolatedSubnets.map(s => s.subnetId).join(','), TaskSecurityGroupId: taskSg.securityGroupId,
      ClusterArn: cluster.clusterArn, TaskDefinitionArn: task.taskDefinitionArn, RepositoryUri: repository.repositoryUri,
      LeaseTable: lease.tableName, StateTable: state.tableName, HistoryTable: history.tableName,
      AlarmTopicArn: topic.topicArn, LogGroup: logGroup.logGroupName })) {
      new cdk.CfnOutput(this, name, { value });
    }
    // CDK's default-SG restriction provider otherwise brings a broad managed
    // logging policy. Give its named function only its own retained log group.
    const providerRole = this.node.findAll().find(node => node instanceof cdk.CfnResource &&
      node.cfnResourceType === 'AWS::IAM::Role' && node.node.path.includes('VpcRestrictDefaultSG')) as cdk.CfnResource | undefined;
    const providerFunction = this.node.findAll().find(node => node instanceof cdk.CfnResource &&
      node.cfnResourceType === 'AWS::Lambda::Function' && node.node.path.includes('VpcRestrictDefaultSG')) as cdk.CfnResource | undefined;
    if (providerRole && providerFunction) {
      const functionName = `${this.stackName}-default-sg-restrictor`;
      providerFunction.addPropertyOverride('FunctionName', functionName);
      providerRole.addPropertyOverride('ManagedPolicyArns', []);
      const providerLogs = new logs.LogGroup(this, 'DefaultSgProviderLogs', {
        logGroupName: `/aws/lambda/${functionName}`, retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.RETAIN,
      });
      const providerPolicy = new iam.CfnPolicy(this, 'DefaultSgProviderLogging', {
        policyName: 'OwnLogStreamsOnly', roles: [providerRole.ref],
        policyDocument: { Version: '2012-10-17', Statement: [{ Effect: 'Allow',
          Action: ['logs:CreateLogStream', 'logs:PutLogEvents'], Resource: providerLogs.logGroupArn }] },
      });
      providerFunction.node.addDependency(providerPolicy, providerLogs);
    }
    // The separately provisioned boundary caps every role, including custom providers.
    for (const child of this.node.findAll()) {
      if (child instanceof cdk.CfnResource && child.cfnResourceType === 'AWS::IAM::Role') {
        child.addPropertyOverride('RoleName', `bot-city-dev-${this.getLogicalId(child).slice(0, 40)}`);
        child.addPropertyOverride('PermissionsBoundary',
          'arn:aws:iam::655469962246:policy/bot-city-dev-workload-boundary');
        // Boundary restricts the default-SG provider to this VPC.
        if (child instanceof iam.CfnRole) {
          cdk.Tags.of(child).add('BotCityVpcId', vpc.vpcId);
        } else {
          child.addPropertyOverride('Tags', [{ Key: 'BotCityVpcId', Value: vpc.vpcId }]);
        }
      }
    }
    // No trading service, schedule or RunTask grant; optional proxy service stays at zero.
  }
}
