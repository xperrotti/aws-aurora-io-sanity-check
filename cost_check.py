import json
import boto3
from datetime import datetime, timedelta
import sys
from dotenv import load_dotenv
import os


def lambda_handler():
    
    standard = 0
    io = 0 

    # Pricing client (not account specific)
    pricing_client = boto3.client('pricing', region_name="us-east-1")

    # get the credentials from user input
    load_dotenv()
    ACCESS_KEY = os.getenv('ACCESS_KEY')
    SECRET_KEY = os.getenv('SECRET_KEY')
    AURORA_REGION = "sa-east-1"
    AURORA_CLUSTER = os.getenv("CLUSTER")

    rds_client = boto3.client(
        'rds', 
        aws_access_key_id = ACCESS_KEY,
        aws_secret_access_key = SECRET_KEY,
        region_name = AURORA_REGION
    )
    
    # Cloudwatch client (account specific)
    cloudwatch_client = boto3.client(
        'cloudwatch', 
        aws_access_key_id = ACCESS_KEY,
        aws_secret_access_key = SECRET_KEY,       
        region_name = AURORA_REGION
    )    
    
    # First, let's figure out the per-instance hourly cost
    
    # Get the list of instances
    response = rds_client.describe_db_instances(
        Filters=[
            {
                'Name': 'db-cluster-id',
                'Values': [
                    AURORA_CLUSTER
                ]
            },
        ],        
    )
    instances = []
    for instance in response['DBInstances']:
        # Get the price per instance for Standard and IO. Assuming an average monthly 730 hours.
        
        # Instance - Standard
    
        price = pricing_client.get_products(
            ServiceCode='AmazonRDS',
            Filters=[
            {
                'Type': 'TERM_MATCH',
                'Field': 'regionCode',
                'Value': AURORA_REGION
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'instanceType',
                'Value': instance['DBInstanceClass']
            },        
            {
                'Type': 'TERM_MATCH',
                'Field': 'enginecode',
                'Value': '21'
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'storage',
                'Value': 'EBS Only'
            }
            ] 
        )
        
        price = json.loads(price['PriceList'][0])
        for plan in price['terms']['OnDemand'].keys():
            for dimension in price['terms']['OnDemand'][plan]['priceDimensions'].keys():
                cost = 730 * float(price['terms']['OnDemand'][plan]['priceDimensions'][dimension]['pricePerUnit']['USD'])
        standard += cost
        
        # Instance - IO
    
        price = pricing_client.get_products(
            ServiceCode='AmazonRDS',
            Filters=[
            {
                'Type': 'TERM_MATCH',
                'Field': 'regionCode',
                'Value': AURORA_REGION
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'instanceType',
                'Value': instance['DBInstanceClass']
            },        
            {
                'Type': 'TERM_MATCH',
                'Field': 'enginecode',
                'Value': '21'
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'storage',
                'Value': 'Aurora IO Optimization Mode'
            }
            ] 
        )
        
        price = json.loads(price['PriceList'][0])
        for plan in price['terms']['OnDemand'].keys():
            for dimension in price['terms']['OnDemand'][plan]['priceDimensions'].keys():
                cost = 730 * float(price['terms']['OnDemand'][plan]['priceDimensions'][dimension]['pricePerUnit']['USD'])
        io += cost       
        
    # Next, let's figure out the storage costs
        
    # The max disk space from the previous day should be a good measure
    disk_metric = cloudwatch_client.get_metric_statistics(
        Namespace='AWS/RDS',
        Dimensions=[
            {
                'Name': 'DBClusterIdentifier',
                'Value': AURORA_CLUSTER
            }
        ],
        MetricName='VolumeBytesUsed',
        StartTime=datetime.now() - timedelta(days=1),
        EndTime=datetime.now(),
        Period=3600,
        Statistics=[
            'Maximum'
        ]
    )        
    
    # Convert to GB    
    disk  = disk_metric['Datapoints'][0]['Maximum']/1024/1024/1024 

    # Find the monthly cost of the storage
    
    # IO Optmized Per GB
    
    price = pricing_client.get_products(
        ServiceCode='AmazonRDS',
        Filters=[
        {
            'Type': 'TERM_MATCH',
            'Field': 'regionCode',
            'Value': AURORA_REGION
        },    
        {
            'Type': 'TERM_MATCH',
            'Field': 'volumeType',
            'Value': 'IO Optimized-Aurora'
        },
        {
            'Type': 'TERM_MATCH',
            'Field': 'enginecode',
            'Value': '21'
        }
        ] 
    )    
    
    price = json.loads(price['PriceList'][0])
    for plan in price['terms']['OnDemand'].keys():
        for dimension in price['terms']['OnDemand'][plan]['priceDimensions'].keys():
            cost = disk * float(price['terms']['OnDemand'][plan]['priceDimensions'][dimension]['pricePerUnit']['USD'])    
    
    io += cost 
    
    # General Purpose Per GB
    
    price = pricing_client.get_products(
        ServiceCode='AmazonRDS',
        Filters=[
        {
            'Type': 'TERM_MATCH',
            'Field': 'regionCode',
            'Value': AURORA_REGION
        },    
        {
            'Type': 'TERM_MATCH',
            'Field': 'volumeType',
            'Value': 'General Purpose-Aurora'
        },
        {
            'Type': 'TERM_MATCH',
            'Field': 'enginecode',
            'Value': '21'
        }
        ] 
    )   

    price = json.loads(price['PriceList'][0])
    for plan in price['terms']['OnDemand'].keys():
        for dimension in price['terms']['OnDemand'][plan]['priceDimensions'].keys():
            cost = disk * float(price['terms']['OnDemand'][plan]['priceDimensions'][dimension]['pricePerUnit']['USD'])    
    
    standard += cost 
    
    # At this point, IO Optimized looks more expensive. Time to find the IO Cost.
    
    # Getting total IOs (read and write)

    ios = 0
        
    io_metric = cloudwatch_client.get_metric_statistics(
        Namespace='AWS/RDS',
        Dimensions=[
            {
                'Name': 'DBClusterIdentifier',
                'Value': AURORA_CLUSTER
            }
        ],
        MetricName='VolumeReadIOPs',
        StartTime=datetime.now() - timedelta(days=30),
        EndTime=datetime.now(),
        Period=3600*24,
        Statistics=[
            'Sum'
        ]
    )         
    
    for datapoint in io_metric['Datapoints']:
        ios += datapoint['Sum']

    io_metric = cloudwatch_client.get_metric_statistics(
        Namespace='AWS/RDS',
        Dimensions=[
            {
                'Name': 'DBClusterIdentifier',
                'Value': AURORA_CLUSTER
            }
        ],
        MetricName='VolumeWriteIOPs',
        StartTime=datetime.now() - timedelta(days=30),
        EndTime=datetime.now(),
        Period=3600*24,
        Statistics=[
            'Sum'
        ]
    )         
    
    for datapoint in io_metric['Datapoints']:
        ios += datapoint['Sum']

    # Figuring IO cost for last month

    # IO Cost
    
    price = pricing_client.get_products(
        ServiceCode='AmazonRDS',
        Filters=[
        {
            'Type': 'TERM_MATCH',
            'Field': 'regionCode',
            'Value': AURORA_REGION
        }, 
        {
            'Type': 'TERM_MATCH',
            'Field': 'group',
            'Value': 'Aurora I/O Operation'
        }, 
        {
            'Type': 'TERM_MATCH',
            'Field': 'enginecode',
            'Value': '21'
        }
        ] 
    )     
        
    price = json.loads(price['PriceList'][0])
    for plan in price['terms']['OnDemand'].keys():
        for dimension in price['terms']['OnDemand'][plan]['priceDimensions'].keys():
            cost = ios * float(price['terms']['OnDemand'][plan]['priceDimensions'][dimension]['pricePerUnit']['USD'])    
    
    standard += cost 

    analysis = {'standard':standard,'io':io}
    print(analysis)

if __name__ == "__main__":
    lambda_handler()
