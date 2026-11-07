#!/usr/bin/env python

import os
import platform
import logging
from functools import wraps

import asyncio
import subprocess
from pathlib import Path

from html import escape

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def authorized(func):
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        user = update.effective_user

        if not user or user.id != ALLOWED_USER_ID or ALLOWED_USER_ID == 0 or update.effective_chat.type != "private":
            logger.warning(
                "Unauthorized access attempt: %s",
                user.id if user else "unknown",
            )
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Access denied."
                )
            return

        return await func(update, context)

    return wrapper


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.effective_message.reply_text(
        f"Your Telegram user ID is: {user.id}"
    )


@authorized
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Welcome to Boursicotage Assistant!\n\n"
        "Use /help to see available commands."
    )


@authorized
async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "Boursicotage commands:\n\n"
        "/ping - Test connection\n"
        "/status - Raspberry Pi information\n"
        "/help - Show this help"
    )


@authorized
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Pong! Pi is reachable.")


@authorized
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = (
        "Boursicotage Raspberry Pi\n\n"
        f"Hostname: {platform.node()}\n"
        f"System: {platform.system()}\n"
        f"Architecture: {platform.machine()}\n"
        f"Python: {platform.python_version()}\n"
        "Bot: Running"
    )
    await update.effective_message.reply_text(response)


@authorized
async def update_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = await update.effective_message.reply_text(
        "Checking Git for updates...\n"
        "I'll restart boursicotage if an update is available."
    )

    script = Path.home() / "apps" / "telegram_bot" / "update_repo.sh"

    if not script.is_file():
        await message.edit_text(
            "ERROR: Update script not found:\n"
            f"{script}"
        )
        return

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [str(script)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    except subprocess.TimeoutExpired:
        await message.edit_text(
            "ERROR: Update operation timed out after 5 minutes.\n"
            "Please check the Raspberry Pi manually."
        )
        return

    except Exception:
        logger.exception("Unexpected error during update")
        await message.edit_text(
            "ERROR: Could not execute the update script. "
            "Check the bot logs."
        )
        return

    output = (result.stdout or "") + (result.stderr or "")
    output = output.strip()

    # Telegram messages have a maximum length.
    if len(output) > 3500:
        output = output[-3500:]

    if result.returncode == 0:
        if "NO_UPDATE:" in output:
            title = "No updates available"
        else:
            title = "Update completed successfully"

    elif result.returncode == 2:
        title = "Update completed, but service restart failed"

    else:
        title = "Update failed"

    await message.edit_text(
        f"{title}\n\n<pre>{escape(output)}</pre>",
        parse_mode="HTML",
    )



def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # This command is temporarily public so you can discover your ID.
    app.add_handler(CommandHandler("whoami", whoami))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("update", update_command))

    logger.info("Starting Boursicotage Telegram bot")
    app.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=-1,
    )


if __name__ == "__main__":
    main()

