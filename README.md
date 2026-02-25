<div align="center">

# 📡 TGLib

<img src="https://img.shields.io/pypi/v/tglib?color=4f6fff&label=PyPI&logo=pypi&logoColor=white&style=for-the-badge" alt="PyPI Version">
<img src="https://img.shields.io/pypi/pyversions/tglib?color=4f6fff&logo=python&logoColor=white&style=for-the-badge" alt="Python Versions">
<img src="https://img.shields.io/github/license/ankit-chaubey/TGLib?color=00e5b0&style=for-the-badge" alt="License">
<img src="https://img.shields.io/github/stars/ankit-chaubey/TGLib?style=for-the-badge&color=ffd060" alt="Stars">
<img src="https://img.shields.io/badge/status-experimental-ff6060?style=for-the-badge" alt="Status">

<br/>
<br/>

**An experimental, full-featured MTProto Python client library for Telegram.**

*Built from scratch · Async-first · Lightweight · Telethon-style API*

<br/>

> ⚠️ **TGLib is in early development.** APIs may change without notice.
> Use in production at your own risk. Contributions and feedback are very welcome!

</div>

---

## ✨ Features

| Feature | Description |
|--------|-------------|
| 🔐 **Full MTProto** | Low-level Telegram protocol implementation |
| ⚡ **Async-first** | Built entirely on `asyncio` |
| 🔄 **Sync & Async** | Use whichever style you prefer |
| 🗄️ **Session Persistence** | SQLite, memory, and string sessions via `aiosqlite` |
| 🔒 **Fast Encryption** | Auto-selects the fastest available AES backend |
| 🤖 **Bot & User Client** | Supports both bot tokens and phone login |
| 📡 **Rich Event System** | NewMessage, Edited, Deleted, CallbackQuery, ChatAction, Raw |
| 🪶 **Lightweight** | Minimal dependencies, maximum control |
| 🐍 **Python 3.10+** | Modern Python features throughout |

---

## 📦 Installation

```bash
# Recommended (with fastest crypto accelerator)
pip install tglib[cipheron]

# Alternative accelerator
pip install tglib[cryptogram]

# Basic install (pure Python fallback)
pip install tglib

# All accelerators — TGLib picks the fastest automatically
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

### 1. Async *(recommended)*

```python
import asyncio, os
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

### 2. Async Context Manager *(cleanest)*

```python
import asyncio, os
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

### 3. Sync *(beginner-friendly, no asyncio needed)*

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

## 🤖 Bot Examples

**Respond to a command:**

```python
import asyncio, os
from tglib import TelegramClient, events

bot = TelegramClient("bot", int(os.environ["API_ID"]), os.environ["API_HASH"])

@bot.on(events.NewMessage(pattern="/start"))
async def start(event):
    await event.reply("Bot is running! 👋")

asyncio.run(bot.start(bot_token=os.environ["BOT_TOKEN"]))
asyncio.run(bot.run_until_disconnected())
```

**Userbot — respond to a pattern:**

```python
from tglib import TelegramClient, events

client = TelegramClient("session", API_ID, API_HASH)

@client.on(events.NewMessage(pattern="(?i)hello"))
async def greet(event):
    await event.reply("Hello there! 👋")

async def main():
    await client.start(phone="+1234567890")
    await client.run_until_disconnected()

asyncio.run(main())
```

---

## 💬 Sessions

```python
# SQLite (default)
client = TelegramClient("my_session", api_id, api_hash)

# In-memory (no disk writes)
from tglib.sessions import MemorySession
client = TelegramClient(MemorySession(), api_id, api_hash)

# String session (env-var / database friendly)
from tglib.sessions import StringSession

# Generate once:
client = TelegramClient(StringSession(), api_id, api_hash)
await client.start(phone="+1234567890")
print(client.session.save())  # → "1AQABAAHd..."  ← store this!

# Reuse:
client = TelegramClient(StringSession("1AQABAAHd..."), api_id, api_hash)
```

---

## 📡 Events

```python
from tglib import events

# New messages with regex pattern
@client.on(events.NewMessage(pattern=r"(?i)^/help"))
async def handler(event):
    await event.reply("Help text here")

# Outgoing messages only
@client.on(events.NewMessage(outgoing=True, pattern="!ping"))
async def pong(event):
    await event.reply("Pong!")

# Edited messages
@client.on(events.MessageEdited)
async def on_edit(event):
    print("Edited:", event.text)

# Deleted messages
@client.on(events.MessageDeleted)
async def on_del(event):
    print("Deleted IDs:", event.deleted_ids)

# Inline button presses (bots)
@client.on(events.CallbackQuery(data=b"btn_ok"))
async def on_btn(event):
    await event.answer("OK!", alert=False)
    await event.edit("Button pressed ✅")

# Chat actions (joins, leaves, title changes)
@client.on(events.ChatAction)
async def on_action(event):
    if event.user_joined:
        await event.respond("Welcome!")

# Raw updates (any type)
@client.on(events.Raw)
async def raw(update):
    print(type(update).__name__, update)

# Filtered raw updates
from tglib.tl import types
@client.on(events.Raw(types.UpdateUserStatus))
async def status_change(update):
    print("Status:", update.status)
```

**Dynamic handler management:**

```python
client.add_event_handler(my_handler, events.NewMessage(pattern="test"))
client.remove_event_handler(my_handler)
```

---

## 📨 Messaging

```python
# Send text (Markdown or HTML)
await client.send_message("me", "**Bold** and _italic_", parse_mode="md")
await client.send_message(chat_id, "<b>HTML</b> <i>text</i>", parse_mode="html")

# Reply, forward, pin
await client.send_message(chat, "Hello", reply_to=msg_id)
await client.forward_messages(target_chat, [msg_id], from_chat)
await client.pin_message(chat, msg_id)
await client.unpin_message(chat, msg_id)
await client.unpin_all_messages(chat)

# Edit and delete
await client.edit_message(chat, msg_id, "Updated text")
await client.delete_messages(chat, [msg_id1, msg_id2], revoke=True)

# Fetch messages
msgs = await client.get_messages(chat, limit=50)
msg  = await client.get_messages(chat, ids=[123, 456])

# Iterate messages
async for msg in client.iter_messages(chat, limit=500):
    print(msg.id, msg.message)

async for msg in client.iter_messages(chat, reverse=True, limit=200):
    ...

async for msg in client.iter_messages(chat, search="hello"):
    ...

messages = await client.iter_messages(chat, limit=100).collect()
```

---

## 📁 Upload & Download

```python
# Send files
await client.send_file(chat, "photo.jpg", caption="My photo")
await client.send_file(chat, "video.mp4", supports_streaming=True)
await client.send_file(chat, "doc.pdf", force_document=True)
await client.send_file(chat, "https://example.com/image.jpg")

# Album (up to 10 at a time)
await client.send_file(chat, ["photo1.jpg", "photo2.jpg", "photo3.jpg"])

# Voice / video note
await client.send_file(chat, "voice.ogg", voice_note=True)
await client.send_file(chat, "round.mp4", video_note=True)

# Download
path = await client.download_media(message)
path = await client.download_media(message, "/downloads/file.jpg")
data = await client.download_media(message, bytes)  # in-memory
path = await client.download_profile_photo("@username")

# Streaming
with open("video.mp4", "wb") as f:
    async for chunk in client.iter_download(message.media):
        f.write(chunk)
```

---

## 🔍 Entity Resolution

```python
user = await client.get_entity("me")
user = await client.get_entity("@username")
user = await client.get_entity("+1234567890")
user = await client.get_entity(123456789)
chat = await client.get_entity(-1001234567890)

# InputPeer (for raw API calls)
peer = await client.get_input_entity("@channel")

# Numeric ID
uid = await client.get_peer_id("@username")
```

---

## ⚙️ Raw API

```python
from tglib.tl import functions, types

# Get full chat info
full = await client(functions.channels.GetFullChannelRequest(
    channel=await client.get_input_entity("@channel")
))

# Update profile
await client(functions.account.UpdateProfileRequest(
    first_name="New Name", bio="tglib user"
))
```

---

## 🔤 Text Formatting

```python
from tglib.extensions import markdown, html

text, entities = markdown.parse("**Bold** and __italic__ and `code`")
md_text = markdown.unparse(text, entities)

text, entities = html.parse("<b>Bold</b> and <a href='https://t.me'>link</a>")
html_text = html.unparse(text, entities)
```

**Markdown syntax:** `**bold**`, `__italic__`, `~~strikethrough~~`, `` `code` ``, `||spoiler||`, `[text](url)`

**HTML tags:** `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a href="...">`, `<blockquote>`, `<spoiler>`

---

## 🗃️ Entity Cache

```python
print(client._entity_cache.stats())
# {'total': 42, 'live': 40, 'expired': 2, 'max_size': 10000, 'ttl': 3600}

from tglib.entitycache import EntityType
client._entity_cache.put(12345, EntityType.USER, access_hash=987654321)
client._entity_cache.invalidate(12345)
```

---

## ♾️ Keep Alive

```python
# Async
await client.run_until_disconnected()

# Sync
client.run(client.run_until_disconnected())
```
---

## 🔧 Dependencies

| Package | Purpose |
|---------|---------|
| `pyaes` | AES encryption for MTProto |
| `pycryptodome` | RSA and additional crypto |
| `aiosqlite` | Async session storage |
| `pillow` *(optional)* | Auto-resize photos before upload |
| `aiofiles` *(optional)* | Async file I/O |

---

## 🤝 Contributing

Contributions are very welcome!

```bash
# Fork the repo, then:
git clone https://github.com/YOUR_USERNAME/TGLib.git
cd TGLib
pip install -e .
```

1. Create a branch: `git checkout -b feature/your-feature`
2. Make your changes
3. Push and open a Pull Request

---

## 🐛 Issues & Feedback

Found a bug or have a suggestion?
👉 [Open an issue](https://github.com/ankit-chaubey/TGLib/issues)

---

## 🙏 Acknowledgements

TGLib builds upon the shoulders of:

- **[Telethon](https://github.com/LonamiWebs/Telethon)** by [Lonami](https://github.com/LonamiWebs) *(MIT License)*
  Core entity resolution logic, upload/download chunking, event system design, HTML/Markdown parsers, string session format, and more. Without Lonami's incredible work, TGLib would not exist.

> *All Telethon-derived code retains its original MIT License. See [LICENSE](https://github.com/LonamiWebs/Telethon/blob/v1/LICENSE) for full details.*

---

## 📄 License

This project is licensed under the **MIT License** see the [LICENSE](./LICENSE) file for details.

---

## 👤 Author

**Ankit Chaubey**
📧 [Email](mailto:ankitchaubey.dev@gmail.com)
🐙 [github.com/ankit-chaubey](https://github.com/ankit-chaubey)
💌 [Telegram](https://t.me/ankify)
---

<div align="center">

Made with ❤️ and Python

</div>
