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
  Built from scratch. Async-first. Lightweight.
</p>

---

## ⚠️ Experimental

> TGLib is currently in early development. APIs may change without notice. Use in production at your own risk. Contributions and feedback are welcome!

---

## ✨ Features

- 🔐 **Full MTProto implementation** — low-level Telegram protocol support
- ⚡ **Async-first** — built entirely on `asyncio`
- 🗄️ **Session persistence** — via `aiosqlite`
- 🔒 **Encryption** — powered by `pyaes` and `pycryptodome`
- 🪶 **Lightweight** — minimal dependencies, maximum control
- 🐍 **Python 3.10+** — uses modern Python features

---

## 📦 Installation

**From PyPI:**
```bash
pip install tglib
```

**From source:**
```bash
git clone https://github.com/ankit-chaubey/TGLib.git
cd TGLib
pip install -e .
```

---

## 🚀 Quick Start

```python
from tglib import Client

client = Client(
    api_id=YOUR_API_ID,
    api_hash="YOUR_API_HASH",
    session="my_session"
)

async def main():
    await client.start()
    me = await client.get_me()
    print(f"Logged in as: {me.first_name}")

client.run(main())
```

> Get your `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org)

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
│   ├── __init__.py       # Entry point
│   ├── client/           # Client logic
│   ├── crypto/           # Encryption & MTProto
│   ├── network/          # TCP transport
│   └── types/            # Telegram types
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

<p align="center">
  Made with ❤️ and Python
</p>
