import os
import asyncio
import subprocess
import requests
import psutil
import shutil
import time
import logging
import re
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

# ------------------------
# Configuration & Globals
# ------------------------
TOKEN = os.getenv("TELEGRAM_TOKEN")

task_queue = asyncio.Queue()
current_task = None
current_file = None
current_process = None          # for cancellation
current_chat = None             # chat id of current download
cancel_requested = False        # flag for upload cancellation

# Simple URL cache for callback data (stores url and mirrors list)
url_cache = {}
CACHE_EXPIRY = 300  # seconds

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ------------------------
# System info
# ------------------------
def get_system_info():
    total, used, free = shutil.disk_usage("/")
    ram = psutil.virtual_memory()
    return {
        "cpu": psutil.cpu_percent(interval=0.1),
        "cpu_cores": psutil.cpu_count(),
        "ram": f"{ram.used // (1024 ** 3)}/{ram.total // (1024 ** 3)} GB",
        "disk": f"{free // (1024 ** 3)} GB Free"
    }

# ------------------------
# Helper: Progress Bar UI
# ------------------------
def create_progress_bar(percentage, length=10):
    filled = int((percentage / 100) * length)
    bar = '█' * filled + '░' * (length - filled)
    return bar

def parse_aria2_line(line):
    # Regex to capture aria2c output: [#123456 1.2MiB/5.0MiB(24%) CN:1 DL:1.2MiB ETA:5s]
    pattern = r'\[.*? ([\d.]+[a-zA-Z]+)/([\d.]+[a-zA-Z]+)\((\d+)%\).*?DL:([\d.]+[a-zA-Z]+)(?:.*?ETA:(.*?))?\]'
    match = re.search(pattern, line)
    if match:
        downloaded, total, pct, speed, eta = match.groups()
        eta = eta if eta else "0s"
        return downloaded, total, int(pct), speed, eta
    return None

# ------------------------
# URL & SourceForge Handlers
# ------------------------
def resolve_direct(url):
    try:
        r = requests.head(url, allow_redirects=True, timeout=10)
        return r.url
    except requests.RequestException:
        return url

def get_sf_mirrors(url):
    try:
        page = requests.get(url, timeout=10)
        soup = BeautifulSoup(page.text, "html.parser")
        mirrors = [option.get("value") for option in soup.select("select#mirrorSelect option") if option.get("value")]
        return mirrors
    except Exception as e:
        logger.error(f"Error fetching SF mirrors: {e}")
        return []

def build_sf_mirror(url, mirror):
    if "sourceforge.net/projects" in url:
        return url.replace("download", f"download?use_mirror={mirror}")
    return url

# ------------------------
# GoFile uploader
# ------------------------
def upload_gofile(file):
    try:
        with open(file, "rb") as f:
            r = requests.post("https://store1.gofile.io/uploadFile", files={"file": f})
        return r.json()["data"]["downloadPage"]
    except Exception as e:
        logger.error(f"Gofile Upload Error: {e}")
        return None

# ------------------------
# Download Worker (aria2c)
# ------------------------
async def download_file(msg, url, filename):
    global current_process
    cmd = [
        "aria2c",
        "-x", "16",
        "-s", "16",
        "--summary-interval=1",
        "--file-allocation=none",
        "--auto-file-renaming=false",
        "--header=User-Agent: Mozilla/5.0",
        "-o", filename,
        url
    ]
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    current_process = process

    cancel_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel Download", callback_data="cancel_download")
    ]])

    last_update = time.time()
    
    while True:
        line = process.stdout.readline()
        if not line:
            break
        
        parsed = parse_aria2_line(line)
        if parsed and (time.time() - last_update > 2):
            last_update = time.time()
            downloaded, total, pct, speed, eta = parsed
            bar = create_progress_bar(pct)
            
            text = (
                f"📥 *Downloading File*\n"
                f"📄 `{filename}`\n\n"
                f"[{bar}] *{pct}%*\n"
                f"💾 *Size:* {downloaded} / {total}\n"
                f"🚀 *Speed:* {speed}\n"
                f"⏱ *ETA:* {eta}"
            )
            try:
                await msg.edit_text(text, parse_mode="Markdown", reply_markup=cancel_keyboard)
            except Exception:
                pass # Ignore message not modified error

    code = process.wait()
    if code != 0 or not os.path.exists(filename):
        raise Exception("Download failed or was cancelled.")
    current_process = None

# ------------------------
# Main Queue Worker
# ------------------------
async def worker(app):
    global current_task, current_file, current_process, current_chat, cancel_requested
    while True:
        task = await task_queue.get()
        chat = task["chat"]
        url = task["url"]
        mirror = task.get("mirror")

        parsed = urlparse(url)
        filename = unquote(os.path.basename(parsed.path))
        if not filename or filename == "download":
            parts = parsed.path.split("/")
            filename = parts[-2] if len(parts) > 2 else f"file_{int(time.time())}"

        current_file = filename
        current_task = "Downloading"
        current_chat = chat
        cancel_requested = False
        
        msg = await app.bot.send_message(chat, f"⏳ *Preparing Download...*\n📄 `{filename}`", parse_mode="Markdown")

        try:
            target_url = build_sf_mirror(url, mirror) if mirror else resolve_direct(url)
            await download_file(msg, target_url, filename)

            if cancel_requested:
                await msg.edit_text("❌ *Operation Cancelled.*", parse_mode="Markdown")
                continue

            current_task = "Uploading"
            upload_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel Upload", callback_data="cancel_upload")
            ]])
            
            await msg.edit_text(
                f"📤 *Uploading to GoFile...*\n📄 `{filename}`\n\n⏳ _Please wait, this process depends on the server connection speed..._", 
                parse_mode="Markdown", 
                reply_markup=upload_keyboard
            )

            loop = asyncio.get_event_loop()
            link = await loop.run_in_executor(None, upload_gofile, filename)

            if cancel_requested:
                await msg.edit_text("❌ *Upload cancelled by user.*", parse_mode="Markdown")
            elif link:
                await msg.edit_text(f"✅ *Mirror Complete!*\n📄 `{filename}`\n🔗 [Download Link]({link})", parse_mode="Markdown")
            else:
                await msg.edit_text("❌ *Upload failed to GoFile.*", parse_mode="Markdown")

        except Exception as e:
            if "cancelled" in str(e).lower():
                await msg.edit_text("❌ *Download cancelled.*", parse_mode="Markdown")
            else:
                await msg.edit_text(f"❌ *Error Occurred:*\n`{e}`", parse_mode="Markdown")

        finally:
            if os.path.exists(filename):
                try:
                    os.remove(filename)
                except Exception as e:
                    logger.error(f"Failed to delete {filename}: {e}")
            
            current_task = None
            current_file = None
            current_process = None
            current_chat = None
            cancel_requested = False
            task_queue.task_done()

# ------------------------
# Commands
# ------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sys = get_system_info()
    text = (
        f"🤖 *VX Mirror Bot Ready*\n\n"
        f"🖥 *System Status:*\n"
        f"├ CPU: {sys['cpu']}% ({sys['cpu_cores']} Cores)\n"
        f"├ RAM: {sys['ram']}\n"
        f"└ Disk: {sys['disk']}\n\n"
        f"📌 *How to use:*\n"
        f"Send `/mirror <file_link>` or *Reply* to a message containing a file link with `/mirror`."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sys = get_system_info()
    queue_size = task_queue.qsize()
    task_info = f"*{current_task}*\n└ `{current_file}`" if current_task else "*Idle*"
    
    text = (
        f"📊 *Bot Status*\n\n"
        f"🖥 *System:*\n"
        f"├ CPU: {sys['cpu']}%\n"
        f"├ RAM: {sys['ram']}\n"
        f"└ Disk: {sys['disk']}\n\n"
        f"⚙️ *Current Task:*\n{task_info}\n\n"
        f"📥 *Queue Size:* {queue_size} tasks"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def mirror(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        url = context.args[0]
    elif update.message.reply_to_message:
        url = update.message.reply_to_message.text
    else:
        await update.message.reply_text("⚠️ *Usage:*\n`/mirror <link>`", parse_mode="Markdown")
        return

    cache_id = str(int(time.time()))
    
    now = time.time()
    expired = [cid for cid, data in url_cache.items() if now - data['ts'] > CACHE_EXPIRY]
    for cid in expired:
        del url_cache[cid]

    if "sourceforge.net/projects" in url:
        mirrors = get_sf_mirrors(url)
        if not mirrors:
            await task_queue.put({"chat": update.effective_chat.id, "url": url})
            await update.message.reply_text("⏳ Added to queue.", parse_mode="Markdown")
            return
        
        if len(mirrors) == 1:
            await task_queue.put({"chat": update.effective_chat.id, "url": url, "mirror": mirrors[0]})
            await update.message.reply_text(f"🌐 *Mirror Auto-Selected:* `{mirrors[0]}`", parse_mode="Markdown")
            return

        # Store mirrors list in cache to avoid 64-byte callback limit
        url_cache[cache_id] = {'url': url, 'ts': time.time(), 'mirrors': mirrors}
        
        buttons = []
        row = []
        for i, m in enumerate(mirrors):
            # Send index instead of full name
            row.append(InlineKeyboardButton(m[:10], callback_data=f"sf|{cache_id}|{i}"))
            if len(row) == 4: # Max 4 buttons per row for better mobile UI
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        await update.message.reply_text(
            "🌐 *Choose SourceForge Mirror:*",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
    else:
        url_cache[cache_id] = {'url': url, 'ts': time.time()}
        buttons = [[
            InlineKeyboardButton("🚀 Mirror File", callback_data=f"link|{cache_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_menu|{cache_id}")
        ]]
        await update.message.reply_text(
            f"👋 *Hi! I’m VX BOT*\n\n🔗 Link detected:\n`{url}`\n\nChoose an action below:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

# ------------------------
# Callback Query Handler
# ------------------------
async def mirror_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_process, current_task, current_file, current_chat, cancel_requested
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("sf|"):
        _, cache_id, mirror_idx = data.split("|")
        cache_data = url_cache.get(cache_id)
        
        if not cache_data:
            await query.edit_message_text("⏰ *This link has expired. Please send it again.*", parse_mode="Markdown")
            return
            
        url = cache_data['url']
        mirror_name = cache_data['mirrors'][int(mirror_idx)]
        
        await query.edit_message_text(f"✅ *Mirror Selected:* `{mirror_name}`\n⏳ Added to queue...", parse_mode="Markdown")
        await task_queue.put({"chat": query.message.chat_id, "url": url, "mirror": mirror_name})
        del url_cache[cache_id]

    elif data.startswith("link|"):
        _, cache_id = data.split("|")
        cache_data = url_cache.get(cache_id)
        
        if not cache_data:
            await query.edit_message_text("⏰ *This link has expired. Please send it again.*", parse_mode="Markdown")
            return
            
        url = cache_data['url']
        await query.edit_message_text("🚀 *Starting mirror...*\n⏳ Added to queue...", parse_mode="Markdown")
        await task_queue.put({"chat": query.message.chat_id, "url": url})
        del url_cache[cache_id]

    elif data == "cancel_download":
        chat_id = query.message.chat_id
        if current_chat == chat_id and current_process:
            current_process.terminate()
            # Cleanup is handled in the `finally` block of the worker function
        else:
            await query.edit_message_text("⚠️ *No active download to cancel.*", parse_mode="Markdown")

    elif data == "cancel_upload":
        chat_id = query.message.chat_id
        if current_chat == chat_id:
            cancel_requested = True
            await query.edit_message_text("❌ *Upload will be cancelled after the current transfer finishes.*", parse_mode="Markdown")
        else:
            await query.edit_message_text("⚠️ *No active upload to cancel.*", parse_mode="Markdown")

    elif data.startswith("cancel_menu|"):
        _, cache_id = data.split("|")
        if cache_id in url_cache:
            del url_cache[cache_id]
        await query.edit_message_text("❌ *Action cancelled.*", parse_mode="Markdown")

# ------------------------
# Main Initialization
# ------------------------
def main():
    if not shutil.which("aria2c"):
        logger.error("aria2c is not installed or not in PATH!")
        return

    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler(["mirror", "m"], mirror))
    app.add_handler(CallbackQueryHandler(mirror_select))

    async def start_worker(app):
        asyncio.create_task(worker(app))

    app.post_init = start_worker
    logger.info("VX BOT STARTED SUCCESSFULLY")
    app.run_polling()

if __name__ == "__main__":
    main()