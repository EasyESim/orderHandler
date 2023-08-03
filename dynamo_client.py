from datetime import datetime
import boto3

class DynamoClient:
    def __init__(self):
        client = boto3.resource('dynamodb')
        self.order_table = client.Table("order")
        self.cust_table = client.Table("customer")
        
    def put_customer(self, customer):
        self.cust_table.put_item(Item={
            'customerId':customer.customerId,
            'shopifyCustomerId':customer.shopifyCustomerId,
            'upsertedAt': str(datetime.now())
        })
    
    def put_order(self, order, customer):
        self.order_table.put_item(Item={
                    'orderId':order.id,
                    'sourceName':order.souce_name,
                    'sourceOrderId': order.source_order_id,
                    'totalPrice': order.price,
                    'orderItems': order.order_items,
                    'upsertedAt': str(datetime.now())
                })
    
        self.cust_table.update_item(
            Key={'customerId': customer.customerId},
            AttributeUpdates={
                'orders': [order.id],
            }
        )

