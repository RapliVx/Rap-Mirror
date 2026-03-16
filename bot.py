import os
import asyncio
import subprocess
import requests
import psutil
import shutil
import time
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

TOKEN = os.getenv("TELEGRAM_TOKEN")
task_queue = asyncio.Queue()
current_task = None
current_file = None

# ------------------------
# System info
# ------------------------
def get_system_info():
    total, used, free = shutil.disk_usage("/")
    ram = psutil.virtual_memory()
    return {
        "cpu": psutil.cpu_count(),
        "ram": f"{ram.used // (1024 ** 3)}/{ram.total // (1024 ** 3)} GB",
        "disk": f"{free // (1024 ** 3)} GB free"
    }

# ------------------------
# Resolve direct link
# ------------------------
def resolve_direct(url):
    try:
        r = requests.head(url, allow_redirects=True, timeout=10)
        return r.url
    except:
        return url

# ------------------------
# SourceForge mirror detection
# ------------------------
def get_sf_mirrors(url):
    try:
        page = requests.get(url, timeout=10)
        soup = BeautifulSoup(page.text, "html.parser")
        mirrors = []
        for option in soup.select("select#mirrorSelect option"):
            mirror_name = option.get("value")
            if mirror_name:
                mirrors.append(mirror_name)
        return mirrors
    except:
        return []

def build_sf_mirror(url, mirror):
    if "sourceforge.net/projects" in url:
        return url.replace("download", f"download?use_mirror={mirror}")
    return url

# ------------------------
# GoFile uploader
# ------------------------
def upload_gofile(file):
    with open(file, "rb") as f:
        r = requests.post("https://store1.gofile.io/uploadFile", files={"file": f})
    try:
        return r.json()["data"]["downloadPage"]
    except:
        return None

# ------------------------
# Download using aria2c
# ------------------------
async def download_file(msg, url, filename):
    global current_file
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

    last_update = time.time()
    while True:
        line = process.stdout.readline()
        if not line:
            break
        if "%" in line and time.time() - last_update > 2:
            last_update = time.time()
            try:
                await msg.edit_text(f"📥 Downloading\n`{filename}`\n\n{line.strip()}", parse_mode="Markdown")
            except:
                pass

    code = process.wait()
    if code != 0 or not os.path.exists(filename):
        raise Exception("Download failed")

# ------------------------
# Worker
# ------------------------
async def worker(app):
    global current_task, current_file
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
        msg = await app.bot.send_message(chat, f"📥 Starting download\n`{filename}`", parse_mode="Markdown")

        try:
            if mirror:
                url = build_sf_mirror(url, mirror)
            else:
                url = resolve_direct(url)
            await download_file(msg, url, filename)

            current_task = "Uploading"
            await msg.edit_text("📤 Uploading...")
            link = upload_gofile(filename)

            if link:
                await msg.edit_text(f"✅ Mirror Complete\n{link}")
            else:
                await msg.edit_text("❌ Upload failed")

        except Exception as e:
            await msg.edit_text(f"❌ Error\n{e}")

        finally:
            if os.path.exists(filename):
                os.remove(filename)
            current_task = None
            current_file = None

# ------------------------
# Commands
# ------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sys = get_system_info()
    await update.message.reply_text(
        f"🤖 Mahiro Mirror Bot Ready\n\n"
        f"CPU : {sys['cpu']}\n"
        f"RAM : {sys['ram']}\n"
        f"Disk : {sys['disk']}\n\n"
        "/mirror <link>\n"
        "/status"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sys = get_system_info()
    queue_size = task_queue.qsize()
    task = f"{current_task}\n{current_file}" if current_task else "Idle"
    await update.message.reply_text(
        f"📊 Bot Status\n\n"
        f"CPU : {sys['cpu']}\n"
        f"RAM : {sys['ram']}\n"
        f"Disk : {sys['disk']}\n\n"
        f"Task : {task}\n"
        f"Queue : {queue_size}"
    )

async def mirror(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        url = context.args[0]
    elif update.message.reply_to_message:
        url = update.message.reply_to_message.text
    else:
        await update.message.reply_text("Usage:\n/mirror <link>")
        return

    if "sourceforge.net/projects" in url:
        mirrors = get_sf_mirrors(url)
        if not mirrors:
            await task_queue.put({"chat": update.effective_chat.id, "url": url})
            return
        if len(mirrors) == 1:
            await task_queue.put({"chat": update.effective_chat.id, "url": url, "mirror": mirrors[0]})
            await update.message.reply_text(f"🌐 Mirror auto selected: {mirrors[0]}")
            return

        # Multiple mirrors → show buttons
        cache_id = str(int(time.time()))
        buttons = []
        row = []
        for i, m in enumerate(mirrors, 1):
            row.append(InlineKeyboardButton(m, callback_data=f"sf|{cache_id}|{m}"))
            if i % 5 == 0:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        await update.message.reply_text(
            "🌐 Choose SourceForge mirror",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return
    else:
        # Direct link or others
        cache_id = str(int(time.time()))
        buttons = [[
            InlineKeyboardButton("🌐 Mirror", callback_data=f"link|{cache_id}"),
            InlineKeyboardButton("⏭ Skip", callback_data="skip")
        ]]
        await update.message.reply_text(
            "👋 *Hi! I’m Mahiro BOT*\nI detected a file link, choose an option below.",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )

# ------------------------
# Callback query handler
# ------------------------
async def mirror_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("sf|"):
        _, cache_id, mirror = data.split("|")
        message = query.message
        await query.message.edit_text(f"🌐 Mirror selected: {mirror}")
        url = ""  # You can store original URL in cache if needed
        await task_queue.put({"chat": query.message.chat_id, "url": url, "mirror": mirror})
    elif data.startswith("link|"):
        message = query.message
        await query.message.edit_text("🌐 Starting mirror...")
        url = ""  # Store or get link from cache
        await task_queue.put({"chat": query.message.chat_id, "url": url})
    elif data == "skip":
        await query.message.edit_text("⏭ Skipped.")

# ------------------------
# Main
# ------------------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler(["mirror", "m"], mirror))
    app.add_handler(CallbackQueryHandler(mirror_select, pattern="^(sf\||link\||skip)"))

    async def start_worker(app):
        asyncio.create_task(worker(app))

    app.post_init = start_worker
    print("BOT STARTED")
    app.run_polling()

if __name__ == "__main__":
    main()