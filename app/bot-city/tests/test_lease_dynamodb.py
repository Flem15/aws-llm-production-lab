"""Exercise the actual boto3 request shapes against an in-process DynamoDB emulator."""
import boto3
from moto import mock_aws
import pytest

from bots.lease import DynamoDbLease, LeaseLost


@mock_aws
def test_conditional_ownership_renewal_release_and_restart():
    client = boto3.client("dynamodb", region_name="us-east-2",
                          aws_access_key_id="testing", aws_secret_access_key="testing")
    client.create_table(TableName="leases",
        KeySchema=[{"AttributeName": "lease_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "lease_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    owner = DynamoDbLease(client, "leases")
    owner.acquire()
    with pytest.raises(LeaseLost):
        DynamoDbLease(client, "leases").acquire()
    owner.renew()
    item = client.get_item(TableName="leases", Key=owner.key, ConsistentRead=True)["Item"]
    assert item["owner"]["S"] == owner.owner
    assert "heartbeat" in item
    owner.release()
    replacement = DynamoDbLease(client, "leases")
    replacement.acquire()
    with pytest.raises(LeaseLost):
        owner.release()
    assert client.get_item(TableName="leases", Key=owner.key)["Item"]["owner"]["S"] == replacement.owner
    replacement.release()


@mock_aws
def test_replaced_owner_cannot_renew_or_delete():
    client = boto3.client("dynamodb", region_name="us-east-2",
                          aws_access_key_id="testing", aws_secret_access_key="testing")
    client.create_table(TableName="leases",
        KeySchema=[{"AttributeName": "lease_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "lease_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    old = DynamoDbLease(client, "leases")
    old.acquire()
    client.put_item(TableName="leases", Item={**old.key, "owner": {"S": "replacement"}})
    with pytest.raises(LeaseLost):
        old.renew()
    with pytest.raises(LeaseLost):
        old.release()
    assert client.get_item(TableName="leases", Key=old.key)["Item"]["owner"]["S"] == "replacement"
