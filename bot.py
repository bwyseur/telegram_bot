#!/usr/bin/env python
"""EXAMPLE of a Telegram bot that uses boursicotage_commands.py. Copy it to where your bot lives and adapt it; it is not run by this project.

Environment of the bot (e.g. a systemd unit's EnvironmentFile):
    BOT_TOKEN=...                    from @BotFather
    ALLOWED_USER_ID=123456789        your Telegram user id (/whoami tells it; then remove that command)
    BOURSICOTAGE_DIR=/home/pi/apps/boursicotage        the checkout, where this plugin and the scripts live
    BOURSICOTAGE_PROFILE=report                        API-key profile in ~/.boursicotage/apikeys.yaml (read-only token is enough)
    BOURSICOTAGE_PYTHON=...          only if boursicotage's virtualenv is not <dir>/venv
"""
import asyncio
import logging
import os
import platform
import subprocess
import sys
from functools import wraps
from html import escape
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
UPDATE_SCRIPT = Path.home() / "apps" / "telegram_bot" / "update_repo.sh"

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs every request URL, and the Telegram URL contains the bot token
logger = logging.getLogger("bot")

# --- the boursicotage commands: optional, so the bot still runs (and can still /update) when boursicotage is missing or broken -----------------
boursicotage = None
if os.environ.get("BOURSICOTAGE_DIR"):
    sys.path.insert(0, os.path.join(os.environ["BOURSICOTAGE_DIR"], "bot_commands"))
    try:
        import boursicotage_commands as boursicotage
    except ImportError:
        logger.exception("boursicotage_commands could not be imported")


def authorized(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user, chat = update.effective_user, update.effective_chat
        if not user or ALLOWED_USER_ID == 0 or user.id != ALLOWED_USER_ID or not chat or chat.type != "private":
            logger.warning("Unauthorized access attempt: %s", user.id if user else "unknown")
            if update.effective_message:
                await update.effective_message.reply_text("Access denied.")
            return
        return await func(update, context)
    return wrapper


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ Public on purpose, to discover your id. Remove it (and the handler below) once ALLOWED_USER_ID is set. """
    await update.effective_message.reply_text(f"Your Telegram user ID is: {update.effective_user.id}")


@authorized
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Welcome to Boursicotage Assistant!\n\nUse /help to see the commands.")


@authorized
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["System:", "/ping - Test connection", "/status - Raspberry Pi information", "/update - Pull the repositories and restart", ""]
    if boursicotage is not None:
        lines += ["Boursicotage (add: eu | us | pdf | text | fresh | a number):"] + boursicotage.help_lines()
    await update.effective_message.reply_text("\n".join(lines + ["", "/help - This help"]))


@authorized
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Pong! Pi is reachable.")


@authorized
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Boursicotage Raspberry Pi\n\nHostname: {platform.node()}\nSystem: {platform.system()}\n"
        f"Architecture: {platform.machine()}\nPython: {platform.python_version()}\nBot: Running")


@authorized
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = await update.effective_message.reply_text("Checking Git for updates...\nI'll restart boursicotage if an update is available.")
    if not UPDATE_SCRIPT.is_file():
        await message.edit_text(f"ERROR: Update script not found:\n{UPDATE_SCRIPT}")
        return
    try:
        result = await asyncio.to_thread(subprocess.run, [str(UPDATE_SCRIPT)], capture_output=True, text=True, timeout=300, check=False)
    except subprocess.TimeoutExpired:
        await message.edit_text("ERROR: Update operation timed out after 5 minutes.\nPlease check the Raspberry Pi manually.")
        return
    except Exception:
        logger.exception("Unexpected error during update")
        await message.edit_text("ERROR: Could not execute the update script. Check the bot logs.")
        return
    output = ((result.stdout or "") + (result.stderr or "")).strip()[-3500:]
    if result.returncode == 0:
        title = "No updates available" if "NO_UPDATE:" in output else "Update completed successfully"
    elif result.returncode == 2:
        title = "Update completed, but service restart failed"
    else:
        title = "Update failed"
    await message.edit_text(f"{title}\n\n<pre>{escape(output)}</pre>", parse_mode="HTML")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """ One place for what no handler caught: logged, and the user is told something went wrong (never the details). """
    logger.error("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Something went wrong; see the bot log.")


async def after_start(application: Application):
    """ Telegram's command menu (the "/" button). """
    menu = [("ping", "Test connection"), ("status", "Raspberry Pi information"), ("update", "Pull the repositories and restart"), ("help", "Help")]
    if boursicotage is not None:
        menu = boursicotage.menu() + menu
    await application.bot.set_my_commands(menu)


def main():
    global boursicotage
    app = Application.builder().token(BOT_TOKEN).post_init(after_start).build()
    app.add_handler(CommandHandler("whoami", whoami))
    for name, handler in (("start", start), ("help", help_command), ("ping", ping), ("status", status), ("update", update_command)):
        app.add_handler(CommandHandler(name, handler))
    app.add_error_handler(on_error)

    if boursicotage is not None:
        try:
            boursicotage.register(app, authorized, bot_token=BOT_TOKEN)
        except boursicotage.ConfigError as error:
            logger.error("Boursicotage commands are off: %s", error)
            boursicotage = None                  # and out of /help and the menu
    logger.info("Starting the Telegram bot")
    app.run_polling(drop_pending_updates=True, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
