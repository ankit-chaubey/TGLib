"""
╔══════════════════════════════════════════════════════╗
║           TGLib Userbot  —  by Ankit Chaubey         ║
║  Powered by TGLib · cipheron · cryptogram            ║
╚══════════════════════════════════════════════════════╝

Commands (prefix: .  — change with BOT_PREFIX in .env)
  .ping           — latency in ms
  .alive          — uptime, account, crypto backend
  .id             — chat / user / message IDs
  .info           — your account details
  .sysinfo        — CPU / RAM / disk / Python
  .backend        — crypto backend table
  .setbackend X   — switch backend at runtime
  .upload <path>  — upload any file with live progress
  .storage <path> — alias for .upload (Android path style)
  .eval <expr>    — evaluate a Python expression
  .exec <code>    — execute Python code
  .sh <cmd>       — run a shell command (30s timeout)
  .delete         — delete the replied-to message
  .purge N        — delete last N messages in this chat
  .forward        — forward replied message → Saved Messages
  .chatinfo       — type and ID of the current chat
  .help           — show this help
"""

# ─────────────────────────────────────────────────────────────────────────────
# stdlib — must come first
# ─────────────────────────────────────────────────────────────────────────────
import asyncio
import io
import logging
import math
import mimetypes
import os
import platform
import sys
import time
import traceback
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# Auto-load .env BEFORE reading os.environ (no external deps needed)
# NOTE: we intentionally use BOT_PREFIX instead of PREFIX to avoid clashing
#       with Termux's own $PREFIX=/data/data/com.termux/files/usr env var.
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))

def _load_dotenv(path: str) -> None:
    if not os.path.isfile(path):
        return
    loaded = []
    with open(path, encoding='utf-8') as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
                loaded.append(key)
    if loaded:
        print(f'📄  Loaded from .env: {", ".join(loaded)}')

_load_dotenv(os.path.join(_HERE, '.env'))

# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap: allow running without pip-installing tglib
# ─────────────────────────────────────────────────────────────────────────────
_TGROOT = os.path.join(_HERE, '..', 'tglib', 'TGLib-main')
if os.path.isdir(_TGROOT) and _TGROOT not in sys.path:
    sys.path.insert(0, os.path.abspath(_TGROOT))

import tglib
from tglib import TelegramClient, set_backend, get_backend, list_backends, print_backends
from tglib.tl.types import (
    UpdateNewMessage, UpdateShortMessage, UpdateShortChatMessage,
    UpdateShort, Updates, UpdatesCombined,
    InputFile, InputFileBig,
    InputMediaUploadedDocument,
    DocumentAttributeFilename, DocumentAttributeVideo, DocumentAttributeAudio,
    InputPeerUser, InputPeerChat, InputPeerChannel, InputPeerSelf,
    PeerUser, PeerChat, PeerChannel,
)
from tglib.tl.functions.upload import SaveFilePartRequest, SaveBigFilePartRequest
from tglib.tl.functions.messages import (
    SendMediaRequest, EditMessageRequest, DeleteMessagesRequest,
    GetHistoryRequest, ForwardMessagesRequest,
)
from tglib import helpers

# ─────────────────────────────────────────────────────────────────────────────
# Config — loaded from .env or environment
# BOT_PREFIX (not PREFIX) avoids the Termux $PREFIX=/data/… collision
# ─────────────────────────────────────────────────────────────────────────────
API_ID   = int(os.environ.get('API_ID', '0'))
API_HASH = os.environ.get('API_HASH', '')
PHONE    = os.environ.get('PHONE', '')
SESSION  = os.environ.get('SESSION_NAME', 'userbot')
PREFIX   = os.environ.get('BOT_PREFIX', '.')          # ← BOT_PREFIX in .env
PREFERRED_BACKEND = os.environ.get('TGLIB_CRYPTO_BACKEND', 'cipheron')

# ─────────────────────────────────────────────────────────────────────────────
# Startup validation
# ─────────────────────────────────────────────────────────────────────────────
def _validate_config() -> None:
    errors = []
    if not API_ID:
        errors.append('  • API_ID   is missing or 0')
    if not API_HASH:
        errors.append('  • API_HASH is missing')
    if not PHONE:
        errors.append('  • PHONE    is missing  (e.g. +91XXXXXXXXXX)')
    if errors:
        print('\n❌  Configuration incomplete. Fix your .env:\n')
        print('\n'.join(errors))
        print('\n📝  .env should contain:')
        print('      API_ID=12345678')
        print('      API_HASH=abcdef...')
        print('      PHONE=+91XXXXXXXXXX')
        print('      BOT_PREFIX=.\n')
        print('    Get API_ID + API_HASH: https://my.telegram.org/apps\n')
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s  %(name)s  %(levelname)s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('userbot')
log.setLevel(logging.INFO)

# ─────────────────────────────────────────────────────────────────────────────
# Global state
# ─────────────────────────────────────────────────────────────────────────────
START_TIME = time.time()
client: TelegramClient = None
_ME_ID: int = 0   # filled after login


# ═════════════════════════════════════════════════════════════════════════════
# MsgCtx — normalized message context
# Unifies Message / UpdateShortMessage / UpdateShortChatMessage into one API
# ═════════════════════════════════════════════════════════════════════════════

class MsgCtx:
    """
    Wraps any TL message/update object and exposes a consistent interface:
      .text       — message text (str)
      .msg_id     — message id (int)
      .out        — True if sent by us (bool)
      .reply_to_id — replied-to message id or None
      .input_peer — ready-to-use InputPeer for edit/send/delete
    """

    def __init__(self, raw):
        self._raw = raw
        self.text        = ''
        self.msg_id      = 0
        self.out         = False
        self.reply_to_id = None
        self.input_peer  = None
        self._parse(raw)

    def _parse(self, raw):
        # ── Full Message object (from UpdateNewMessage) ──────────────────────
        if isinstance(raw, type) and hasattr(raw, 'peer_id'):
            pass  # handled below via hasattr

        if hasattr(raw, 'peer_id') and raw.peer_id is not None:
            # Full Message
            self.text    = getattr(raw, 'message', '') or ''
            self.msg_id  = raw.id
            self.out     = bool(getattr(raw, 'out', False))
            self.input_peer = _peer_to_input(raw.peer_id)
            rt = getattr(raw, 'reply_to', None)
            if rt:
                self.reply_to_id = getattr(rt, 'reply_to_msg_id', None)

        elif isinstance(raw, UpdateShortMessage):
            # Private message (DM)
            self.text    = raw.message or ''
            self.msg_id  = raw.id
            self.out     = bool(raw.out)
            # peer is the other user in a DM; for our own outgoing msg the
            # peer_id = our target user
            self.input_peer = InputPeerUser(user_id=raw.user_id, access_hash=0)
            rt = getattr(raw, 'reply_to', None)
            if rt:
                self.reply_to_id = getattr(rt, 'reply_to_msg_id', None)

        elif isinstance(raw, UpdateShortChatMessage):
            # Group chat message
            self.text    = raw.message or ''
            self.msg_id  = raw.id
            self.out     = bool(raw.out)
            self.input_peer = InputPeerChat(chat_id=raw.chat_id)
            rt = getattr(raw, 'reply_to', None)
            if rt:
                self.reply_to_id = getattr(rt, 'reply_to_msg_id', None)


def _peer_to_input(peer):
    """Convert a Peer* TL object to the matching InputPeer*."""
    if isinstance(peer, PeerUser):
        return InputPeerUser(user_id=peer.user_id, access_hash=0)
    if isinstance(peer, PeerChat):
        return InputPeerChat(chat_id=peer.chat_id)
    if isinstance(peer, PeerChannel):
        return InputPeerChannel(channel_id=peer.channel_id, access_hash=0)
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Core helpers
# ═════════════════════════════════════════════════════════════════════════════

def uptime_str() -> str:
    elapsed = int(time.time() - START_TIME)
    h, rem  = divmod(elapsed, 3600)
    m, s    = divmod(rem, 60)
    return f'{h}h {m}m {s}s'


def human_size(n: int) -> str:
    if n < 1024:
        return f'{n} B'
    for unit in ('KB', 'MB', 'GB', 'TB'):
        n /= 1024
        if n < 1024:
            return f'{n:.1f} {unit}'
    return f'{n:.1f} PB'


def progress_bar(done: int, total: int, width: int = 16) -> str:
    pct  = done / total if total else 0
    fill = int(width * pct)
    return f'[{"█"*fill}{"░"*(width-fill)}] {pct*100:.1f}%'


async def edit(ctx: MsgCtx, text: str) -> None:
    """Edit the triggering message in-place — core of userbot style."""
    if ctx.input_peer is None:
        log.warning('edit: input_peer is None for msg %s', ctx.msg_id)
        return
    try:
        await client(EditMessageRequest(
            peer=ctx.input_peer,
            id=ctx.msg_id,
            message=text,
            no_webpage=True,
        ))
    except Exception as e:
        log.error('edit failed: %s', e)


async def delete_msg(ctx: MsgCtx, extra_ids: list = None) -> None:
    ids = [ctx.msg_id] + (extra_ids or [])
    if ctx.input_peer is None:
        return
    try:
        await client(DeleteMessagesRequest(id=ids, revoke=True))
    except Exception as e:
        log.error('delete failed: %s', e)


# ═════════════════════════════════════════════════════════════════════════════
# Peer resolution — editing own msgs works with access_hash=0, but
# SendMediaRequest / ForwardMessages need the REAL access_hash.
# ═════════════════════════════════════════════════════════════════════════════

async def resolve_send_peer(ctx: MsgCtx):
    """
    Return an InputPeer with a real access_hash for sending NEW messages.

    Now that the library caches entities from every incoming Updates object,
    we just need to:
      1. Saved Messages (self DM)  → InputPeerSelf()   (no hash needed)
      2. Basic group               → InputPeerChat()    (no hash needed)
      3. User / Channel / SG       → session cache, then API call
    """
    raw = ctx._raw

    # ── Determine peer type and numeric ID ────────────────────────────────
    if isinstance(raw, UpdateShortMessage):
        peer_type, peer_id = 'user', raw.user_id
    elif isinstance(raw, UpdateShortChatMessage):
        peer_type, peer_id = 'chat', raw.chat_id
    else:
        peer = getattr(raw, 'peer_id', None)
        if peer is None:
            return ctx.input_peer
        if isinstance(peer, PeerUser):
            peer_type, peer_id = 'user', peer.user_id
        elif isinstance(peer, PeerChat):
            peer_type, peer_id = 'chat', peer.chat_id
        elif isinstance(peer, PeerChannel):
            peer_type, peer_id = 'channel', peer.channel_id
        else:
            return ctx.input_peer

    # ── Fast paths that never need a hash ─────────────────────────────────
    if peer_type == 'user' and peer_id == _ME_ID:
        return InputPeerSelf()
    if peer_type == 'chat':
        return InputPeerChat(chat_id=peer_id)

    # ── Session cache (populated by the fixed library dispatcher) ─────────
    row = client.session.get_entity_rows_by_id(peer_id)
    if row and row[1]:          # row[1] is access_hash — only use if non-zero
        _, access_hash = row
        if peer_type == 'user':
            return InputPeerUser(user_id=peer_id, access_hash=access_hash)
        return InputPeerChannel(channel_id=peer_id, access_hash=access_hash)

    # ── API resolution (library now uses GetChannelsRequest for channels) ──
    try:
        peer_obj = getattr(raw, 'peer_id', None) or ctx.input_peer
        return await client.get_input_entity(peer_obj)
    except Exception as e:
        log.warning('resolve_send_peer API fallback failed: %s', e)

    return ctx.input_peer


# ═════════════════════════════════════════════════════════════════════════════
# File upload engine
# ═════════════════════════════════════════════════════════════════════════════

CHUNK   = 512 * 1024       # 512 KB per part
BIG_THR = 10 * 1024 * 1024 # files > 10 MB → BigFile API


async def upload_file(path: str, progress_cb=None):
    size    = os.path.getsize(path)
    # Must be signed=True: SaveFilePartRequest serialises file_id with
    # struct.pack("<q", ...) which requires a signed int64.  signed=False
    # produced values > 2^63-1 half the time, crashing the MTProto connection.
    file_id = helpers.generate_random_long(signed=True)
    big     = size > BIG_THR
    parts   = math.ceil(size / CHUNK)

    with open(path, 'rb') as f:
        for part in range(parts):
            chunk = f.read(CHUNK)
            if not chunk:
                break
            if big:
                req = SaveBigFilePartRequest(
                    file_id=file_id, file_part=part,
                    file_total_parts=parts, bytes=chunk)
            else:
                req = SaveFilePartRequest(
                    file_id=file_id, file_part=part, bytes=chunk)
            ok = await client(req)
            if not ok:
                raise RuntimeError(f'Upload rejected at part {part}/{parts}')
            if progress_cb:
                await progress_cb(min((part + 1) * CHUNK, size), size)

    name = os.path.basename(path)
    if big:
        return InputFileBig(id=file_id, parts=parts, name=name)
    return InputFile(id=file_id, parts=parts, name=name, md5_checksum='')


def media_attrs(path: str):
    ext   = os.path.splitext(path)[1].lower()
    name  = os.path.basename(path)
    attrs = [DocumentAttributeFilename(file_name=name)]
    if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v'):
        attrs.append(DocumentAttributeVideo(
            duration=0, w=0, h=0, supports_streaming=True))
    elif ext in ('.mp3', '.m4a', '.ogg', '.flac', '.wav', '.aac', '.opus'):
        attrs.append(DocumentAttributeAudio(
            duration=0, voice=(ext == '.ogg')))
    return attrs


def guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    return mime or 'application/octet-stream'


# ═════════════════════════════════════════════════════════════════════════════
# Command handlers — each receives (ctx: MsgCtx, args: str)
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_ping(ctx, _args):
    t0 = time.time()
    await edit(ctx, '🏓 Pinging…')
    ms = (time.time() - t0) * 1000
    await edit(ctx, f'🏓 **Pong!** `{ms:.2f} ms`')


async def cmd_alive(ctx, _args):
    info = get_backend()
    me   = await client.get_me()
    name = f'{me.first_name or ""} {me.last_name or ""}'.strip()
    await edit(ctx,
        f'⚡ **TGLib Userbot** is alive!\n\n'
        f'👤 Account : `{name}` (`{me.id}`)\n'
        f'⏱ Uptime  : `{uptime_str()}`\n'
        f'🔐 Crypto  : `{info["name"]}` '
        f'(HW: {"✅" if info["hw_accel"] else "❌"})\n'
        f'🐍 Python  : `{sys.version.split()[0]}`\n'
        f'📦 tglib   : `{tglib.__version__}`'
    )


async def cmd_id(ctx, _args):
    raw  = ctx._raw
    peer = getattr(raw, 'peer_id', None)
    pid  = None
    if peer:
        pid = (getattr(peer, 'user_id', None)
               or getattr(peer, 'chat_id', None)
               or getattr(peer, 'channel_id', None))
    elif isinstance(raw, UpdateShortMessage):
        pid = raw.user_id
    elif isinstance(raw, UpdateShortChatMessage):
        pid = raw.chat_id

    from_id = getattr(raw, 'from_id', None)
    sid = None
    if from_id:
        sid = getattr(from_id, 'user_id', None)

    lines = ['🆔 **IDs**\n']
    lines.append(f'• Chat ID   : `{pid}`')
    if sid:
        lines.append(f'• Sender ID : `{sid}`')
    lines.append(f'• My ID     : `{_ME_ID}`')
    lines.append(f'• Msg ID    : `{ctx.msg_id}`')
    await edit(ctx, '\n'.join(lines))


async def cmd_info(ctx, _args):
    me    = await client.get_me()
    name  = f'{me.first_name or ""} {me.last_name or ""}'.strip()
    uname = f'@{me.username}' if getattr(me, 'username', None) else '—'
    phone = getattr(me, 'phone', '—')
    await edit(ctx,
        f'👤 **Account Info**\n\n'
        f'• Name     : `{name}`\n'
        f'• Username : {uname}\n'
        f'• Phone    : `+{phone}`\n'
        f'• ID       : `{me.id}`\n'
        f'• Bot?     : `{bool(getattr(me, "bot", False))}`\n'
        f'• Premium? : `{bool(getattr(me, "premium", False))}`'
    )


async def cmd_sysinfo(ctx, _args):
    await edit(ctx, '⏳ Gathering system info…')
    try:
        import psutil
        cpu  = psutil.cpu_percent(interval=0.5)
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
        up   = str(datetime.now(tz=timezone.utc) - boot).split('.')[0]
        text = (
            f'🖥 **System Info**\n\n'
            f'• OS    : `{platform.system()} {platform.release()}`\n'
            f'• Arch  : `{platform.machine()}`\n'
            f'• CPU   : `{cpu:.1f}%` ({psutil.cpu_count()} cores)\n'
            f'• RAM   : `{human_size(mem.used)}/{human_size(mem.total)}` '
            f'(`{mem.percent}%`)\n'
            f'• Disk  : `{human_size(disk.used)}/{human_size(disk.total)}` '
            f'(`{disk.percent}%`)\n'
            f'• Sys up: `{up}`\n'
            f'• Bot up: `{uptime_str()}`\n'
            f'• Python: `{sys.version.split()[0]}`'
        )
    except ImportError:
        text = (
            f'🖥 **System Info** _(pip install psutil for full stats)_\n\n'
            f'• OS    : `{platform.system()} {platform.release()}`\n'
            f'• Arch  : `{platform.machine()}`\n'
            f'• Python: `{sys.version.split()[0]}`\n'
            f'• Bot up: `{uptime_str()}`'
        )
    await edit(ctx, text)


async def cmd_backend(ctx, _args):
    rows  = list_backends()
    lines = ['🔐 **Crypto Backends**\n']
    for r in rows:
        icon = '▶' if r['active'] else ('✔' if r['installed'] else '✗')
        hw   = '⚡' if r['hw_accel'] else '  '
        caps = ', '.join(sorted(r['supports'])) or '—'
        lines.append(f'`{icon} {hw} {r["name"]:12s}` {caps}')
    lines.append(f'\n_Active: **{get_backend()["name"]}**_')
    await edit(ctx, '\n'.join(lines))


async def cmd_setbackend(ctx, args):
    name = args.strip().lower()
    if not name:
        await edit(ctx, '❌ Usage: `.setbackend <name>`\n'
                        'Choices: ' + ', '.join(f'`{b}`' for b in tglib.BACKENDS))
        return
    try:
        set_backend(name)
        info = get_backend()
        await edit(ctx,
            f'✅ Switched → `{info["name"]}`\n'
            f'⚡ HW accel : `{info["hw_accel"]}`\n'
            f'🔩 Detail   : `{info["hw_detail"]}`'
        )
    except ValueError as e:
        await edit(ctx, f'❌ {e}')


async def cmd_upload(ctx, args):
    path = args.strip().strip('"').strip("'")
    if not path:
        await edit(ctx, '❌ Usage: `.upload /path/to/file`')
        return
    if not os.path.isfile(path):
        await edit(ctx, f'❌ File not found:\n`{path}`')
        return

    size      = os.path.getsize(path)
    name      = os.path.basename(path)
    last_edit = [0.0]

    await edit(ctx,
        f'📤 **Uploading…**\n'
        f'📄 `{name}`\n'
        f'📦 {human_size(size)}\n\n'
        f'{progress_bar(0, size)}'
    )

    async def on_progress(done: int, total: int):
        if time.time() - last_edit[0] < 2.5:
            return
        last_edit[0] = time.time()
        await edit(ctx,
            f'📤 **Uploading…**\n'
            f'📄 `{name}`\n'
            f'📦 {human_size(done)} / {human_size(total)}\n\n'
            f'{progress_bar(done, total)}'
        )

    t0 = time.time()
    try:
        input_file = await upload_file(path, progress_cb=on_progress)
    except Exception as e:
        await edit(ctx, f'❌ Upload failed:\n`{e}`')
        return

    elapsed = time.time() - t0
    speed   = size / elapsed if elapsed > 0 else 0
    caption = f'📄 {name}  •  {human_size(size)}  •  {elapsed:.1f}s @ {human_size(int(speed))}/s'

    # Resolve real access_hash — access_hash=0 causes PEER_ID_INVALID on send
    send_peer = await resolve_send_peer(ctx)
    try:
        await client(SendMediaRequest(
            peer=send_peer,
            media=InputMediaUploadedDocument(
                file=input_file,
                mime_type=guess_mime(path),
                attributes=media_attrs(path),
                force_file=False,
            ),
            message=caption,
            random_id=helpers.generate_random_long(),
        ))
        await delete_msg(ctx)
    except Exception as e:
        await edit(ctx, f'⚠️ Uploaded but send failed: `{e}`')


async def cmd_eval(ctx, args):
    code = args.strip()
    if not code:
        await edit(ctx, '❌ Usage: `.eval <expr>`')
        return
    try:
        result = eval(code, {'client': client, 'ctx': ctx})  # noqa
        if asyncio.iscoroutine(result):
            result = await result
        await edit(ctx, f'**>>>** `{code}`\n\n`{result}`')
    except Exception:
        await edit(ctx, f'❌ **Error**\n```\n{traceback.format_exc(limit=3)[-900:]}\n```')


async def cmd_exec(ctx, args):
    code = args.strip()
    if not code:
        await edit(ctx, '❌ Usage: `.exec <code>`')
        return
    buf = io.StringIO()
    sys.stdout, old = buf, sys.stdout
    try:
        exec(compile(code, '<exec>', 'exec'),  # noqa
             {'client': client, 'ctx': ctx, 'asyncio': asyncio})
        output = buf.getvalue() or '_(no output)_'
        await edit(ctx, f'✅ **exec**\n```\n{output[-1000:]}\n```')
    except Exception:
        await edit(ctx, f'❌ **Error**\n```\n{traceback.format_exc(limit=3)[-900:]}\n```')
    finally:
        sys.stdout = old


async def cmd_shell(ctx, args):
    cmd = args.strip()
    if not cmd:
        await edit(ctx, '❌ Usage: `.sh <command>`')
        return
    await edit(ctx, f'⏳ `$ {cmd}`')
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output    = stdout.decode(errors='replace').strip() or '_(no output)_'
        await edit(ctx,
            f'🖥 `$ {cmd}`\n```\n{output[-1500:]}\n```'
            f'\nExit: `{proc.returncode}`'
        )
    except asyncio.TimeoutError:
        await edit(ctx, '❌ Command timed out (30s)')
    except Exception as e:
        await edit(ctx, f'❌ `{e}`')


async def cmd_delete(ctx, _args):
    if ctx.reply_to_id is None:
        await edit(ctx, '❌ Reply to a message to delete it.')
        return
    await delete_msg(ctx, extra_ids=[ctx.reply_to_id])


async def cmd_purge(ctx, args):
    try:
        n = int(args.strip())
        assert 1 <= n <= 200
    except Exception:
        await edit(ctx, '❌ Usage: `.purge <1–200>`')
        return
    if ctx.input_peer is None:
        return
    history = await client(GetHistoryRequest(
        peer=ctx.input_peer, limit=n + 5,
        offset_id=0, offset_date=0, add_offset=0,
        min_id=0, max_id=0, hash=0,
    ))
    msgs = getattr(history, 'messages', [])
    ids  = [m.id for m in msgs if m is not None]
    if ids:
        await client(DeleteMessagesRequest(id=ids, revoke=True))


async def cmd_forward(ctx, _args):
    if ctx.reply_to_id is None:
        await edit(ctx, '❌ Reply to a message to forward it to Saved Messages.')
        return
    if ctx.input_peer is None:
        return
    from_peer = await resolve_send_peer(ctx)
    await client(ForwardMessagesRequest(
        from_peer=from_peer,
        id=[ctx.reply_to_id],
        to_peer=InputPeerSelf(),
        random_id=[helpers.generate_random_long()],
    ))
    await delete_msg(ctx)


async def cmd_chatinfo(ctx, _args):
    raw  = ctx._raw
    peer = getattr(raw, 'peer_id', None)
    if peer:
        ptype = type(peer).__name__
        pid   = (getattr(peer, 'user_id', None)
                 or getattr(peer, 'chat_id', None)
                 or getattr(peer, 'channel_id', None))
    elif isinstance(raw, UpdateShortMessage):
        ptype, pid = 'PeerUser (DM)', raw.user_id
    elif isinstance(raw, UpdateShortChatMessage):
        ptype, pid = 'PeerChat (Group)', raw.chat_id
    else:
        ptype, pid = 'Unknown', '?'

    lines = ['💬 **Chat Info**\n',
             f'• Type : `{ptype}`',
             f'• ID   : `{pid}`']
    if 'Channel' in str(ptype):
        lines.append(f'• Full ID : `-100{pid}`')
    await edit(ctx, '\n'.join(lines))


async def cmd_help(ctx, _args):
    await edit(ctx,
        f'**TGLib Userbot**  |  prefix: `{PREFIX}`\n\n'
        f'`{PREFIX}ping`             — latency\n'
        f'`{PREFIX}alive`            — uptime & version\n'
        f'`{PREFIX}id`               — chat/user IDs\n'
        f'`{PREFIX}info`             — account info\n'
        f'`{PREFIX}sysinfo`          — CPU/RAM/disk\n'
        f'`{PREFIX}backend`          — crypto backends\n'
        f'`{PREFIX}setbackend <n>`   — switch backend\n'
        f'`{PREFIX}upload <path>`    — upload file\n'
        f'`{PREFIX}storage <path>`   — upload (Android alias)\n'
        f'`{PREFIX}eval <expr>`      — Python eval\n'
        f'`{PREFIX}exec <code>`      — Python exec\n'
        f'`{PREFIX}sh <cmd>`         — shell command\n'
        f'`{PREFIX}delete`           — delete replied msg\n'
        f'`{PREFIX}purge <N>`        — delete last N msgs\n'
        f'`{PREFIX}forward`          — fwd reply → Saved\n'
        f'`{PREFIX}chatinfo`         — current chat info\n'
        f'`{PREFIX}help`             — this message\n\n'
        f'_Crypto: cipheron → cryptogram → cryptg → pyaes_'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Command routing table
# ─────────────────────────────────────────────────────────────────────────────
COMMANDS = {
    'ping':       cmd_ping,
    'alive':      cmd_alive,
    'id':         cmd_id,
    'info':       cmd_info,
    'sysinfo':    cmd_sysinfo,
    'backend':    cmd_backend,
    'setbackend': cmd_setbackend,
    'upload':     cmd_upload,
    'storage':    cmd_upload,
    'eval':       cmd_eval,
    'exec':       cmd_exec,
    'sh':         cmd_shell,
    'shell':      cmd_shell,
    'delete':     cmd_delete,
    'del':        cmd_delete,
    'purge':      cmd_purge,
    'forward':    cmd_forward,
    'fwd':        cmd_forward,
    'chatinfo':   cmd_chatinfo,
    'help':       cmd_help,
}


# ═════════════════════════════════════════════════════════════════════════════
# Update normalizer — pulls raw messages out of any TL envelope
# ═════════════════════════════════════════════════════════════════════════════

def extract_raw_messages(update):
    """Yield raw message objects from any TL update wrapper."""
    if isinstance(update, (Updates, UpdatesCombined)):
        for u in update.updates:
            yield from extract_raw_messages(u)
    elif isinstance(update, UpdateShort):
        yield from extract_raw_messages(update.update)
    elif isinstance(update, UpdateNewMessage):
        yield update.message
    elif isinstance(update, (UpdateShortMessage, UpdateShortChatMessage)):
        yield update


# ═════════════════════════════════════════════════════════════════════════════
# Main update handler
# ═════════════════════════════════════════════════════════════════════════════

async def handle_update(update):
    for raw in extract_raw_messages(update):
        ctx = MsgCtx(raw)

        # Only handle our own outgoing messages
        if not ctx.out:
            continue

        text = ctx.text
        if not text.startswith(PREFIX):
            continue

        parts   = text[len(PREFIX):].split(None, 1)
        cmd_key = parts[0].lower() if parts else ''
        args    = parts[1] if len(parts) > 1 else ''

        handler = COMMANDS.get(cmd_key)
        if handler is None:
            continue

        log.info('CMD: %s%s  args=%r', PREFIX, cmd_key, args[:60])
        try:
            await handler(ctx, args)
        except Exception:
            tb = traceback.format_exc(limit=4)
            log.error('Handler %s raised:\n%s', cmd_key, tb)
            try:
                await edit(ctx, f'❌ **Error in `{cmd_key}`**\n```\n{tb[-800:]}\n```')
            except Exception:
                pass


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

async def main():
    global client, _ME_ID

    _validate_config()

    # Set crypto backend
    try:
        set_backend(PREFERRED_BACKEND)
        log.info('Crypto backend: %s', PREFERRED_BACKEND)
    except ValueError:
        log.warning('Backend %r not available, using auto-select.', PREFERRED_BACKEND)

    client = TelegramClient(
        session=SESSION,
        api_id=API_ID,
        api_hash=API_HASH,
        device_model='TGLib Userbot',
        system_version='Linux',
        app_version=tglib.__version__,
    )

    # Register catch-all handler
    client.on(None)(handle_update)

    await client.connect()

    if not await client.is_user_authorized():
        log.info('Not authorised — starting login flow.')
        print(f'\n📱  Sending code to {PHONE} …')
        try:
            sent = await client.send_code_request(PHONE)
        except Exception as e:
            print(f'\n❌  Failed to send code: {e}')
            print('    Double-check API_ID, API_HASH and PHONE in .env')
            await client.disconnect()
            sys.exit(1)

        code = input('🔑  Enter the Telegram code: ').strip()
        try:
            await client.sign_in(PHONE, code, phone_code_hash=sent.phone_code_hash)
        except Exception as exc:
            msg_lower = str(exc).lower()
            if 'password' in msg_lower or 'session_password' in msg_lower or '2fa' in msg_lower:
                pwd = input('🔒  Enter 2FA password: ').strip()
                await client._sign_in_2fa(pwd)
            else:
                print(f'\n❌  Sign-in failed: {exc}')
                await client.disconnect()
                sys.exit(1)

    me     = await client.get_me()
    _ME_ID = me.id
    name   = f'{me.first_name or ""} {me.last_name or ""}'.strip()

    print_backends()
    log.info('✅  Signed in as %s (id=%s)', name, me.id)
    log.info('⚡  Userbot running — prefix: %s', PREFIX)

    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n👋 Stopped.')
