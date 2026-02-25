"""
tglib/extensions/markdown.py  —  Markdown <-> Telegram entity parser.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
import re
from typing import List, Tuple, Union

from ..helpers import add_surrogate, del_surrogate, within_surrogate, strip_text
from ..tl.tlobject import TLObject
from ..tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode,
    MessageEntityPre, MessageEntityTextUrl, MessageEntityMentionName,
    MessageEntityStrike, MessageEntityUnderline, MessageEntitySpoiler,
)

# Defined locally so it survives generate_tl.py regeneration.
# TypeMessageEntity is only used for type hints.
try:
    from ..tl.types import TypeMessageEntity  # noqa: F401
except ImportError:
    TypeMessageEntity = Union[
        MessageEntityBold, MessageEntityItalic, MessageEntityCode,
        MessageEntityPre, MessageEntityTextUrl, MessageEntityMentionName,
        MessageEntityStrike, MessageEntityUnderline, MessageEntitySpoiler,
    ]

# Default Markdown delimiters  →  entity class
DEFAULT_DELIMITERS = {
    '**': MessageEntityBold,
    '__': MessageEntityItalic,
    '~~': MessageEntityStrike,
    '||': MessageEntitySpoiler,
    '`':  MessageEntityCode,
    '```': MessageEntityPre,
}

DEFAULT_URL_RE     = re.compile(r'\[([^\]]*?)\]\([\s\S]*?\)')
DEFAULT_URL_FORMAT = '[{0}]({1})'


def parse(
    message: str,
    delimiters: dict = None,
    url_re=None,
) -> Tuple[str, List[TypeMessageEntity]]:
    """
    Parse Markdown-formatted *message* into (plain_text, entity_list).

    Supported syntax:
        **bold**, __italic__, ~~strikethrough~~, ||spoiler||,
        `inline code`, ```pre block```, [link text](url),
        [mention](tg://user?id=123)
    """
    if not message:
        return message, []

    if url_re is None:
        url_re = re.compile(r'\[([^\]]*?)\]\(([\s\S]*?)\)')
    elif isinstance(url_re, str):
        url_re = re.compile(url_re)

    if delimiters is None:
        delimiters = DEFAULT_DELIMITERS
    elif not delimiters:
        return message, []

    # Build a regex that matches any delimiter (longest first to avoid
    # ``` being matched as three ` characters)
    delim_re = re.compile('|'.join(
        '({})'.format(re.escape(k))
        for k in sorted(delimiters, key=len, reverse=True)
    ))

    message = add_surrogate(message)
    result: List[TypeMessageEntity] = []
    i = 0

    while i < len(message):
        m = delim_re.match(message, pos=i)
        if m:
            delim = next(filter(None, m.groups()))
            end   = message.find(delim, i + len(delim) + 1)

            if end != -1:
                # Remove the opening and closing delimiters
                message = ''.join((
                    message[:i],
                    message[i + len(delim):end],
                    message[end + len(delim):]
                ))

                # Adjust already-found entity lengths
                for ent in result:
                    if ent.offset + ent.length > i:
                        if (ent.offset <= i
                                and ent.offset + ent.length >= end + len(delim)):
                            ent.length -= len(delim) * 2
                        else:
                            ent.length -= len(delim)

                ent_cls = delimiters[delim]
                if ent_cls == MessageEntityPre:
                    result.append(ent_cls(i, end - i - len(delim), ''))
                else:
                    result.append(ent_cls(i, end - i - len(delim)))

                # No nested entities inside code blocks
                if ent_cls in (MessageEntityCode, MessageEntityPre):
                    i = end - len(delim)
                continue

        elif url_re:
            m = url_re.match(message, pos=i)
            if m:
                link_text = m.group(1)
                link_url  = m.group(2)
                message = ''.join((
                    message[:m.start()],
                    link_text,
                    message[m.end():]
                ))

                delta = m.end() - m.start() - len(link_text)
                for ent in result:
                    if ent.offset + ent.length > m.start():
                        ent.length -= delta

                if link_url.startswith('tg://user?id='):
                    try:
                        uid = int(link_url.split('=', 1)[1])
                        result.append(MessageEntityMentionName(
                            offset=m.start(), length=len(link_text), user_id=uid))
                    except (ValueError, IndexError):
                        result.append(MessageEntityTextUrl(
                            offset=m.start(), length=len(link_text),
                            url=del_surrogate(link_url)))
                else:
                    result.append(MessageEntityTextUrl(
                        offset=m.start(), length=len(link_text),
                        url=del_surrogate(link_url)))

                i += len(link_text)
                continue

        i += 1

    message = strip_text(message, result)
    return del_surrogate(message), result


def unparse(
    text: str,
    entities,
    delimiters: dict = None,
    url_fmt: str = None,
) -> str:
    """
    Reconstruct Markdown from *text* and its MessageEntity list.

    This is the reverse of :func:`parse`.
    """
    if not text or not entities:
        return text

    if delimiters is None:
        delimiters = DEFAULT_DELIMITERS
    elif not delimiters:
        return text

    if isinstance(entities, TLObject):
        entities = (entities,)

    text = add_surrogate(text)
    inv_delimiters = {v: k for k, v in delimiters.items()}
    insert_at = []

    for i, entity in enumerate(entities):
        s = entity.offset
        e = entity.offset + entity.length
        delim = inv_delimiters.get(type(entity))
        if delim:
            insert_at.append((s,  i,  delim))
            insert_at.append((e, -i,  delim))
        else:
            url = None
            if isinstance(entity, MessageEntityTextUrl):
                url = entity.url
            elif isinstance(entity, MessageEntityMentionName):
                url = f'tg://user?id={entity.user_id}'
            if url:
                insert_at.append((s,  i,  '['))
                insert_at.append((e, -i, f']({url})'))

    insert_at.sort(key=lambda t: (t[0], t[1]))

    while insert_at:
        at, _, what = insert_at.pop()
        while at < len(text) and within_surrogate(text, at):
            at += 1
        text = text[:at] + what + text[at:]

    return del_surrogate(text)
