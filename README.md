# Mahiro Mirror Bot

A fast and reliable Telegram bot to mirror files from **SourceForge** and other direct links to **GoFile**. It supports automatic detection of SourceForge mirrors, interactive buttons for mirror selection, and direct file uploads without manual intervention.

---

## Features

* 🌐 **SourceForge Mirror Detection**
  Automatically detects all available mirrors for SourceForge files and lets users select their preferred mirror through buttons.

* 📥 **Direct Download Support**
  Works with direct download links, Google Drive, and other common hosting platforms.

* 🟢 **GoFile Integration**
  All mirrored files are uploaded to GoFile for easy sharing.

* 🤖 **Telegram Interaction**
  Provides a user-friendly interface via Telegram commands and inline buttons.

* ⚡ **Progress Tracking**
  Shows live download and upload progress in Telegram messages.

* 🔄 **Automatic Mirror Trigger**
  Any message containing a valid link triggers the bot to ask the user if they want to mirror the file.

---

## Commands

| Command                       | Description                                                    |
| ----------------------------- | -------------------------------------------------------------- |
| `/m` or `/mirror <link>`      | Mirror a SourceForge link, with mirror selection if available. |
| `/status`                     | Show bot system status and queue information.                  |
| Any message containing a link | The bot will respond with a friendly prompt to mirror or skip. |

---

## Requirements

* Python 3.12+
* python-telegram-bot `==20.3`
* requests
* psutil
* beautifulsoup4
* lxml

Install dependencies with:

```bash
pip install -r requirements.txt
```

---

## Setup

1. Clone the repository:

```bash
git clone https://github.com/yourusername/mahiro-mirror-bot.git
cd mahiro-mirror-bot
```

2. Set your environment variables:

```bash
export TELEGRAM_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
export PIXEL_TOKEN="YOUR_PIXEL_TOKEN"   # Optional if using Pixel integration
```

3. Run the bot:

```bash
python bot.py
```

---

## Usage

* Simply send a SourceForge or direct link to the bot.
* For SourceForge links, if multiple mirrors are available, the bot will present buttons for selection.
* For GoFile uploads, files are automatically uploaded and the GoFile link is returned.

---

## Screenshots

```
User: /m <sourceforge link>
Bot: 🌐 Choose SourceForge mirror
      [Mirror1] [Mirror2] [Mirror3]

User clicks mirror → Bot shows download progress
Bot: 📥 Downloading: file.img
Bot: 📤 Uploading...
Bot: ✅ Mirror Complete: GoFile link
```

---

## Contributing

Pull requests and suggestions are welcome!
Make sure your code is clean, well-commented, and follows Python best practices.

---

## License

This project is licensed under the **MIT License** – see [LICENSE](LICENSE) for details.
