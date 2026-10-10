import os

from dotenv import load_dotenv
from twilio.rest import Client


def main():
    load_dotenv()


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


    message = client.messages.create(
        body="good morning",
        from_=whatsapp_from,
        to=whatsapp_to
    )


    print(
        "Message SID:",
        message.sid
    )


if __name__ == "__main__":
    main()
