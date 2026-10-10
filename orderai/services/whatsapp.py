import os

from dotenv import load_dotenv
from twilio.rest import Client


load_dotenv()


def send_whatsapp_message(message):
    account_sid = os.getenv(
        "TWILIO_ACCOUNT_SID"
    )

    auth_token = os.getenv(
        "TWILIO_AUTH_TOKEN"
    )

    whatsapp_from = os.getenv(
        "TWILIO_WHATSAPP_FROM"
    )

    whatsapp_to = os.getenv(
        "TWILIO_WHATSAPP_TO"
    )

    client = Client(
        account_sid,
        auth_token
    )

    sent_message = client.messages.create(
        body=message,
        from_=whatsapp_from,
        to=whatsapp_to
    )

    return {
        "status": "sent",
        "message_sid": sent_message.sid
    }