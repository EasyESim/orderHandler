from dynamo_client import DynamoClient
from customer import Customer
from order import Order
from esim_go_client import EsimGoClient
from send_email import EmailClient
import logging

# Initialize logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    raw_payload = event['detail']['payload']
    order = Order(raw_payload)
    dynamo_client = DynamoClient()
    email_client = EmailClient()
    esim_client = EsimGoClient()

    # Check if the source_order_id exists in the database
    existing_order = dynamo_client.get_order_by_source_order_id(order.source_order_id)
    if existing_order:
        current_status = existing_order['status']
        if current_status == "esim_order_created":
            dynamo_client.update_order_status(existing_order['order_id'], "dynamodb_esim_order_creation_failed")
            return
        elif current_status == "esim_order_saved":
            dynamo_client.update_order_status(existing_order['order_id'], "esim_details_retrieval_failed")
            return
        elif current_status == "order_saved":
            dynamo_client.delete_order(existing_order['order_id'])

    existing_customers = dynamo_client.get_customers(raw_payload['customer']['id'])
    if len(existing_customers) > 1:
        logger.error("Multiple customers returned for id: %s", str(raw_payload['customer']['id']))
        return

    if len(existing_customers) == 0:
        cust = Customer.from_payload(raw_payload, order)
    else:
        cust = Customer.from_dynamo(existing_customers[0])
        cust.addOrder(order)

    put_customer_success = dynamo_client.put_customer(cust)
    if not put_customer_success:
        logger.error("Failed to save new customer: %s", str(cust))
        return

    put_order_success = dynamo_client.put_order(order, cust)
    if not put_order_success:
        logger.error("Failed to save new order: %s", str(order))
        return

    logger.info("Order Success")
    dynamo_client.update_order_status(order.id, "order_saved")

    esim_order_details = esim_client.new_order(order)
    if not esim_order_details:
        logger.error("Failed to generate a new order in EsimGo: %s", str(order))
        dynamo_client.update_order_status(order.id, "esim_order_creation_failed")
        email_client.send_email_on_failure("Failed to generate a new order in EsimGo", str(order))
        return

    logger.info("Esim Order Success")
    dynamo_client.update_order_status(order.id, "esim_order_created")

    order_id = dynamo_client.put_esim_order(esim_order_details, order, raw_payload['contact_email'], raw_payload['order_number'])
    if not order_id:
        logger.error("Failed to generate a new order: %s", str(esim_order_details))
        dynamo_client.update_order_status(order.id, "dynamodb_esim_order_creation_failed")
        email_client.send_email_on_failure("Failed to generate a new order", str(esim_order_details))
        return
    else:
        logger.info("Esim Order Updated with ID: %s", order_id)

    dynamo_client.update_order_status(order.id, "esim_order_saved")

    esim_order_details = esim_client.get_esim_details(order_id)
    if not esim_order_details:
        logger.error("Failed to get eSIM details: %s", str(esim_order_details))
        dynamo_client.update_order_status(order.id, "esim_details_retrieval_failed")
        email_client.send_email_on_failure("Failed to get eSIM details", str(esim_order_details))
        return
    else:
        response = dynamo_client.update_esim_order(order_id, esim_order_details, raw_payload['line_items'])

    image_data = esim_client.get_esim_qrcode(order_id)
    if not image_data:
        logger.error("Failed to get eSIM QR code details: %s", str(esim_order_details))
        dynamo_client.update_order_status(order.id, "esim_qrcode_retrieval_failed")
        email_client.send_email_on_failure("Failed to get eSIM QR code details", str(esim_order_details))
        return
    else:
        response = dynamo_client.update_esim_qr_code(order_id, image_data)

    logger.info("Get Esim QR code Success")
    dynamo_client.update_order_status(order.id, "esim_qrcode_retrieved")

    qr_codes = dynamo_client.get_qr_code_from_db(order_id)
    if not qr_codes:
        logger.error("Failed to get QR codes from the database: %s", str(qr_codes))
        dynamo_client.update_order_status(order.id, "dynamodb_qrcode_retrieval_failed")
        email_client.send_email_on_failure("Failed to get QR codes from the database", str(qr_codes))
        return

    esim_details = dynamo_client.get_esim_from_db(order_id)
    if not esim_details:
        logger.error("Failed to get eSIM details from the database: %s", str(esim_details))
        dynamo_client.update_order_status(order.id, "dynamodb_esim_details_retrieval_failed")
        email_client.send_email_on_failure("Failed to get eSIM details from the database", str(esim_details))
        return

    logger.info("Sending email to client")
    if qr_codes:
        email_client.send_email_with_qr_code(raw_payload['contact_email'], qr_codes, esim_details, raw_payload['order_number'])
        dynamo_client.update_order_status(order.id, "email_sent")
        
        #Update eSIM details
        update_success = esim_client.update_esim(esim_details, raw_payload['order_number'])
        
        if not update_success:
            logger.error("Failed to update eSIM details: %s", str(esim_details))
            dynamo_client.update_order_status(order.id, "email_sent_and_update_esim_ref_failed")
            email_client.send_email_on_failure("Failed to update eSIM Reference after email sent", str(esim_details))
        else:
            logger.info("eSIM details updated successfully")
            
    else:
        logger.warning('QR code data not found in DynamoDB')
        dynamo_client.update_order_status(order.id, "qrcode_data_not_found")
