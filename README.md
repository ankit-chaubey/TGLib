# TGLib

<p align="center">
  <img src="https://img.shields.io/pypi/v/tglib?color=blue&label=PyPI&logo=pypi&logoColor=white" alt="PyPI Version">
  <img src="https://img.shields.io/pypi/pyversions/tglib?color=blue&logo=python&logoColor=white" alt="Python Versions">
  <img src="https://img.shields.io/github/license/ankit-chaubey/TGLib?color=green" alt="License">
  <img src="https://img.shields.io/github/stars/ankit-chaubey/TGLib?style=social" alt="Stars">
  <img src="https://img.shields.io/badge/status-experimental-orange" alt="Status">
</p>

<p align="center">
  <b>An experimental, full-featured MTProto Python client library for Telegram.</b><br>
  Built from scratch. Async-first. Lightweight. Telethon-style API.
</p>

---

## ⚠️ Experimental

> TGLib is currently in early development. APIs may change without notice. Use in production at your own risk. Contributions and feedback are welcome!

---

## ✨ Features

- 🔐 **Full MTProto implementation** — low-level Telegram protocol support
- ⚡ **Async-first** — built entirely on `asyncio`
- 🔄 **Sync & Async support** — use whichever style you prefer
- 🗄️ **Session persistence** — via `aiosqlite`
- 🔒 **Encryption** — powered by `cipheron` (ARM-CE/AES-NI) › `cryptogram` › `cryptg` › `pycryptodome` › `pyaes`
- 🤖 **Bot & User client** — supports both bot tokens and phone login
- 🪶 **Lightweight** — minimal dependencies, maximum control
- 🐍 **Python 3.10+** — uses modern Python features

---

## 📦 Installation

**From PyPI (with recommended crypto accelerator):**
```bash
pip install tglib[cipheron]
```

**From PyPI (with alternative accelerator):**
```bash
pip install tglib[cryptogram]
```

**Basic install (falls back to pure Python):**
```bash
pip install tglib
```

**Install all accelerators and let TGLib pick the fastest:**
```bash
pip install tglib[all]
```

**From source:**
```bash
git clone https://github.com/ankit-chaubey/TGLib.git
cd TGLib
pip install -e .[cipheron]
```

### 🔒 Crypto Backend Priority

TGLib automatically selects the fastest available AES/factorization backend:

| Priority | Package | Description |
|----------|---------|-------------|
| 1st ✅ | `cipheron` | ARM-CE / AES-NI via OpenSSL EVP — **recommended** |
| 2nd | `cryptogram` | AES-NI with pure-Python fallback |
| 3rd | `cryptg` | Legacy C extension (compatibility) |
| 4th | `pycryptodome` | CTR/CBC only; IGE via Python |
| 5th | `pyaes` | Pure-Python fallback (always available) |

---

## 🚀 Quick Start

Get your `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org).  
**Never hardcode credentials — use environment variables!**

```bash
export API_ID=your_api_id
export API_HASH=your_api_hash
```

---

## 📖 Usage Styles

TGLib supports three usage styles, just like Telethon.

### 1. Async (recommended)

```python
import asyncio
import os
from tglib import TelegramClient

client = TelegramClient(
    session="my_session",
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"],
)

async def main():
    await client.start(phone="+1234567890")
    me = await client.get_me()
    print(f"Logged in as: {me.first_name}")
    await client.disconnect()

asyncio.run(main())
```

---

### 2. Async Context Manager (cleanest)

```python
import asyncio
import os
from tglib import TelegramClient

async def main():
    async with TelegramClient(
        session="my_session",
        api_id=int(os.environ["API_ID"]),
        api_hash=os.environ["API_HASH"],
    ) as client:
        me = await client.get_me()
        print(f"Logged in as: {me.first_name}")

asyncio.run(main())
```

---

### 3. Sync (beginner-friendly, no asyncio needed)

```python
import os
from tglib import TelegramClient

with TelegramClient(
    session="my_session",
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"],
) as client:
    me = client.run(client.get_me())
    print(f"Logged in as: {me.first_name}")
```

---

## 🤖 Bot Example

```python
import asyncio
import os
from tglib import TelegramClient

client = TelegramClient(
    session="bot_session",
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"],
)

@client.on(None)  # handle all updates
async def handler(update):
    print(update)

async def main():
    await client.start(bot_token=os.environ["BOT_TOKEN"])
    print("Bot is running...")
    await client.run_until_disconnected()

asyncio.run(main())
```

---

## 📡 Sending Messages

```python
# Send a plain message
await client.send_message("username", "Hello from TGLib!")

# Send with markdown
await client.send_message("username", "**Bold** and *italic*", parse_mode="md")

# Send with HTML
await client.send_message("username", "<b>Bold</b> text", parse_mode="html")

# Reply to a message
await client.send_message("username", "Replying!", reply_to=message_id)
```

---

## 📥 Receiving Messages

```python
async def main():
    await client.start(phone="+1234567890")

    # Get recent messages
    messages = await client.get_messages("username", limit=10)
    for msg in messages.messages:
        print(msg.message)

    # Get dialogs
    dialogs = await client.get_dialogs(limit=20)

asyncio.run(main())
```

---

## 🔄 run_until_disconnected

Keep your client or bot alive until manually stopped (`Ctrl+C`):

```python
# Async
await client.run_until_disconnected()

# Sync
client.run(client.run_until_disconnected())
```

---

## 🔧 Dependencies

| Package | Purpose |
|--------|---------|
| `pyaes` | AES encryption for MTProto |
| `pycryptodome` | RSA and additional crypto |
| `aiosqlite` | Async session storage |

---

## 📁 Project Structure

```
TGLib/
├── tglib/
│   ├── __init__.py          # Entry point
│   ├── client.py            # Main TelegramClient
│   ├── crypto/              # Encryption & MTProto crypto
│   ├── network/             # TCP transport & MTProto sender
│   ├── sessions/            # SQLite & Memory sessions
│   ├── tl/                  # TL schema types & functions
│   │   ├── types/           # Telegram object types
│   │   └── functions/       # Telegram API functions
│   ├── errors/              # RPC & custom errors
│   └── helpers.py           # Utility functions
├── setup.py
└── README.md
```

---

## 🤝 Contributing

Contributions are very welcome! Here's how to get started:

```bash
# Fork the repo, then:
git clone https://github.com/YOUR_USERNAME/TGLib.git
cd TGLib
pip install -e .
```

1. Create a new branch: `git checkout -b feature/your-feature`
2. Make your changes
3. Push and open a Pull Request

---

## 🐛 Issues & Feedback

Found a bug or have a suggestion?  
👉 [Open an issue](https://github.com/ankit-chaubey/TGLib/issues)

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

## 👤 Author

**Ankit Chaubey**  
📧 [ankitchaubey.dev@gmail.com](mailto:ankitchaubey.dev@gmail.com)  
🐙 [github.com/ankit-chaubey](https://github.com/ankit-chaubey)

---

<p align="center">Made with ❤️ and Python</p>
