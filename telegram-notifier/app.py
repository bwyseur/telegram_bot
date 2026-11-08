
import os
import secrets
import logging

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

# Configuration
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_KEY = os.environ["NOTIFIER_API_KEY"]

# Named destinations and their Telegram chat IDs
CHAT_IDS = {
    key: value
    for key, value in (
        item.split("=", 1)
        for item in os.environ["TELEGRAM_CHAT_IDS"].split(",")
    )
}

TELEGRAM_URL = (
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram-notifier")

app = FastAPI(title="Central Telegram Notification Service")


class Notification(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    target: str = "default"
    service: str = "unknown"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/notify")
async def notify(
    notification: Notification,
    x_api_key: str = Header(default="")
):
    # Authenticate caller
    if not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )

    # Resolve destination
    chat_id = CHAT_IDS.get(notification.target)

    if not chat_id:
        raise HTTPException(
            status_code=400,
            detail="Unknown notification target"
        )

    # Build message
    text = (
        f"🔔 {notification.service}\n\n"
        f"{notification.message}"
    )

    # Send through Telegram
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                TELEGRAM_URL,
                json={
                    "chat_id": chat_id,
                    "text": text
                }
            )

            response.raise_for_status()
            result = response.json()

            if not result.get("ok"):
                raise HTTPException(
                    status_code=502,
                    detail="Telegram rejected the message"
                )

    except httpx.HTTPError:
        logger.exception("Telegram delivery failed")
        raise HTTPException(
            status_code=502,
            detail="Telegram delivery failed"
        )

    logger.info(
        "Notification sent: service=%s target=%s",
        notification.service,
        notification.target
    )

    return {
        "status": "sent",
        "service": notification.service,
        "target": notification.target
    }

