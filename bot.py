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

# System services whose logs can be retrieved through Telegram.
LOG_SERVICES = {
    "boursicotage": "boursicotage.service",
    "telegram_bot": "telegram-bot.service",
}

JOURNALCTL = "/usr/bin/journalctl"
SUDO = "/usr/bin/sudo"


logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs every request URL, and the Telegram URL contains the bot token
logger = logging.getLogger("bot")

# --- Boursicotage commands: optional -----------------------------------------
# --- the bot still runs (and can still /update) when boursicotage is missing or broken
boursicotage = None
if os.environ.get("BOURSICOTAGE_DIR"):
    sys.path.insert(0, os.path.join(os.environ["BOURSICOTAGE_DIR"], "bot_commands"))
    try:
        import boursicotage_commands as boursicotage
    except ImportError:
        logger.exception("boursicotage_commands could not be imported")

# --- Authorization -----------------------------------------------------------
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


# --- Basic commands ----------------------------------------------------------
async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ Public on purpose, to discover your id. Remove it (and the handler below) once ALLOWED_USER_ID is set. """
    await update.effective_message.reply_text(f"Your Telegram user ID is: {update.effective_user.id}")


@authorized
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Welcome to Boursicotage Assistant!\n\nUse /help to see the commands.")


@authorized
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [
	"System:", 
	"/ping - Test connection", 
	"/status - Raspberry Pi information", 
	"/update - Pull the repositories and restart", 
        "/log_boursicotage - Show boursicotage logs",
        "/log_telegram_bot - Show Telegram bot logs",
	"",
    ]
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


# --- Update command ----------------------------------------------------------
@authorized
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = await update.effective_message.reply_text(
	"Checking Git for updates...\n"
	"I'll restart boursicotage if an update is available.")

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

# --- Service log commands ----------------------------------------------------
async def send_service_logs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    service_name: str
):
    """Retrieve and send the last 100 lines of a systemd service log."""

    message = await update.effective_message.reply_text(
        f"Retrieving logs for {service_name}..."
    )

    command = [
        SUDO,
        "-n",
        JOURNALCTL,
        "-u",
        service_name,
        "-n",
        "100",
        "--no-pager"
    ]

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False
        )

    except subprocess.TimeoutExpired:
        await message.edit_text(
            f"ERROR: Retrieving {service_name} logs timed out."
        )
        return

    except Exception:
        logger.exception("Unexpected error retrieving service logs")

        await message.edit_text(
            f"ERROR: Could not retrieve logs for {service_name}."
        )
        return

    if result.returncode != 0:
        logger.error(
            "journalctl failed for %s: %s",
            service_name,
            result.stderr
        )

        error = (result.stderr or "Unknown error").strip()[-2000:]

        await message.edit_text(
            f"ERROR retrieving logs for {service_name}:\n{error}"
        )
        return

    output = result.stdout.strip()

    if not output:
        output = "No logs found."



    # Highlight WARNING, ERROR and CRITICAL log entries.
    # Use Telegram-supported HTML formatting.

    formatted_lines = []

    for line in output.splitlines():
        upper_line = line.upper()

        if "CRITICAL" in upper_line:
            formatted = f"‼ <b>{escape(line)}</b>"

        elif "ERROR" in upper_line:
            formatted = f"🔴 <b>{escape(line)}</b>"

        elif "WARNING" in upper_line:
            formatted = f"⚠️ <b>{escape(line)}</b>"

        else:
            formatted = escape(line)

        formatted_lines.append(formatted)

    # Split into Telegram-safe message chunks.
    max_length = 3500
    chunks = []
    current_chunk = []

    for line in formatted_lines:
        candidate = "\n".join(current_chunk + [line])

        if len(candidate) > max_length and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
        else:
            current_chunk.append(line)

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    await message.edit_text(
        f"Logs for {service_name} ({len(chunks)} message(s))."
    )

    for index, chunk in enumerate(chunks, start=1):
        await update.effective_message.reply_text(
            f"<b>[{index}/{len(chunks)}]</b>\n\n{chunk}",
            parse_mode="HTML",
            disable_web_page_preview=True
        )


    """
    # Telegram allows a maximum of 4096 characters per message.
    # Leave room for the heading.
    max_length = 3800

    chunks = [
        output[i:i + max_length]
        for i in range(0, len(output), max_length)
    ]

    await message.edit_text(
        f"Logs for {service_name} ({len(chunks)} message(s))."
    )

    for index, chunk in enumerate(chunks, start=1):
        await update.effective_message.reply_text(
            f"[{index}/{len(chunks)}]\n\n{chunk}",
            disable_web_page_preview=True
        )
   """

@authorized
async def log_boursicotage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await send_service_logs(
        update,
        context,
        LOG_SERVICES["boursicotage"]
    )


@authorized
async def log_telegram_bot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await send_service_logs(
        update,
        context,
        LOG_SERVICES["telegram_bot"]
    )


# --- Error handling ----------------------------------------------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """ One place for what no handler caught: logged, and the user is told something went wrong (never the details). """
    logger.error("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Something went wrong; see the bot log.")


# --- Telegram command menu ---------------------------------------------------
async def after_start(application: Application):
    """ Telegram's command menu (the "/" button). """
    menu = [
	("ping", "Test connection"), 
	("status", "Raspberry Pi information"), 
	("update", "Pull the repositories and restart"), 
        ("log_boursicotage", "Show boursicotage logs"),
        ("log_telegram_bot", "Show Telegram bot logs"),
	("help", "Help")]
    if boursicotage is not None:
        menu = boursicotage.menu() + menu
    await application.bot.set_my_commands(menu)


# --- Main --------------------------------------------------------------------
def main():
    global boursicotage
    app = Application.builder().token(BOT_TOKEN).post_init(after_start).build()

    # Public command to discover your Telegram user ID.
    app.add_handler(CommandHandler("whoami", whoami))

    # Standard commands.
    handlers = [
        ("start", start),
        ("help", help_command),
        ("ping", ping),
        ("status", status),
        ("update", update_command),
        ("log_boursicotage", log_boursicotage),
        ("log_telegram_bot", log_telegram_bot),
    ]
    for name, handler in handlers: 
        app.add_handler(CommandHandler(name, handler))
    app.add_error_handler(on_error)

    # Register optional boursicotage commands.
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
