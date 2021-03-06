import json
import os
import time
import logging
import boto3
from botocore.exceptions import ClientError
from common import *
from subscription import *
from azure.mgmt.subscription import SubscriptionClient


# Setup Logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('boto3').setLevel(logging.WARNING)
# TODO - Figure out what stupid module Azure uses for logger so I can suppress all their damn debug messages.


def handler(event, context):
    logger.info("Received event: " + json.dumps(event, sort_keys=True))

    dynamodb = boto3.resource('dynamodb')
    subscription_table = dynamodb.Table(os.environ['SUBSCRIPTION_TABLE'])

    azure_secrets = get_azure_creds(os.environ['AZURE_SECRET_NAME'])
    
    if azure_secrets is None:
        raise Exception("Unable to extract Azure Credentials. Aborting...")

    collected_subs = []
    for tenant, credential_info in azure_secrets.items():

        azure_creds = ServicePrincipalCredentials(
            client_id=credential_info["application_id"],
            secret=credential_info["key"],
            tenant=credential_info["tenant_id"]
        )

        resource_client = SubscriptionClient(azure_creds)
        
        for subscription in resource_client.subscriptions.list():

            # Some subscrption ID's retured by the API are not queryable, this seems like a bug with MS API.
            # There may also be a better way of determining this...
            queryable = 'false'
            
            if 'Access to Azure Active Directory' not in subscription.display_name:
                
                # Keep track of all valid subscriptions
                collected_subs.append(subscription.subscription_id)
                queryable = 'true'
                
            subscription_dict = {
                "subscription_id": subscription.subscription_id,
                "display_name": subscription.display_name,
                "state": subscription.state,
                "SubscriptionClass": json.loads(json.dumps(subscription, default=str)),
                "tenant_id": credential_info["tenant_id"],
                "tenant_name": tenant,
                "queryable": queryable
            }

            # Add subscriptions to DynamoDB subscriptions table.
            create_or_update_subscription(subscription_dict, subscription_table)

        if collected_subs is None:
            raise Exception("No Subscriptions found. Aborting...")
    
    # Return only valid subscription ID's to be sent via SNS by inventory trigger function
    event['subscription_list'] = collected_subs
    return(event)


def create_or_update_subscription(subscription, subscription_table):
    logger.info(u"Adding subscription {}".format(subscription))

    try:
        response = subscription_table.update_item(
            Key= {'subscription_id': subscription["subscription_id"]},
            UpdateExpression="set display_name=:name, subscription_state=:status, SubscriptionClass=:class_record, tenant_id=:tenant_id, tenant_name=:tenant_name, queryable=:queryable",
            ExpressionAttributeValues={
                ':name':            subscription["display_name"],
                ':status':          subscription["state"],
                ':class_record':    subscription["SubscriptionClass"],
                ':tenant_id':       subscription["tenant_id"],
                ':tenant_name':     subscription["tenant_name"],
                ':queryable':       subscription["queryable"]
            }
        )

    except ClientError as e:
        raise AccountUpdateError(u"Unable to create {}: {}".format(subscription, e))
    except KeyError as e:
        logger.critical(f"Subscription {subscription} is missing a key: {e}")


class AccountUpdateError(Exception):
    '''raised when an update to DynamoDB Fails'''
