"""
tglib/extensions/html.py  —  HTML <-> Telegram entity parser.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
from collections import deque
from html import escape
from html.parser import HTMLParser
from typing import List, Tuple, Union

from ..helpers import add_surrogate, del_surrogate, within_surrogate, strip_text
from ..tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode,
    MessageEntityPre, MessageEntityEmail, MessageEntityUrl,
    MessageEntityTextUrl, MessageEntityMentionName,
    MessageEntityUnderline, MessageEntityStrike, MessageEntityBlockquote,
    MessageEntityCustomEmoji, MessageEntitySpoiler,
    MessageEntityMention, MessageEntityBotCommand,
    MessageEntityHashtag, MessageEntityCashtag, MessageEntityPhone,
    MessageEntityBankCard, MessageEntityUnknown,
)

# Defined locally so it survives generate_tl.py regeneration.
# TypeMessageEntity is only used for type hints.
try:
    from ..tl.types import TypeMessageEntity  # noqa: F401 – available in production zip
except ImportError:
    TypeMessageEntity = Union[
        MessageEntityBold, MessageEntityItalic, MessageEntityCode,
        MessageEntityPre, MessageEntityEmail, MessageEntityUrl,
        MessageEntityTextUrl, MessageEntityMentionName,
        MessageEntityUnderline, MessageEntityStrike, MessageEntityBlockquote,
        MessageEntityCustomEmoji, MessageEntitySpoiler, MessageEntityMention,
        MessageEntityBotCommand, MessageEntityHashtag, MessageEntityCashtag,
        MessageEntityPhone, MessageEntityBankCard, MessageEntityUnknown,
    ]


class HTMLToTelegramParser(HTMLParser):
    """Stateful HTML parser that produces Telegram MessageEntity objects."""

    def __init__(self):
        super().__init__()
        self.text = ''
        self.entities: List[TypeMessageEntity] = []
        self._building_entities: dict = {}
        self._open_tags: deque = deque()
        self._open_tags_meta: deque = deque()

    def handle_starttag(self, tag, attrs):
        self._open_tags.appendleft(tag)
        self._open_tags_meta.appendleft(None)

        attrs = dict(attrs)
        EntityType = None
        args = {}

        if tag in ('strong', 'b'):
            EntityType = MessageEntityBold
        elif tag in ('em', 'i'):
            EntityType = MessageEntityItalic
        elif tag == 'u':
            EntityType = MessageEntityUnderline
        elif tag in ('del', 's', 'strike'):
            EntityType = MessageEntityStrike
        elif tag == 'blockquote':
            EntityType = MessageEntityBlockquote
        elif tag == 'spoiler':
            EntityType = MessageEntitySpoiler
        elif tag == 'code':
            # Inside <pre>, this marks a language for syntax highlighting
            if 'pre' in self._building_entities:
                pre = self._building_entities['pre']
                lang_class = attrs.get('class', '')
                if lang_class.startswith('language-'):
                    pre.language = lang_class[len('language-'):]
                return
            EntityType = MessageEntityCode
        elif tag == 'pre':
            EntityType = MessageEntityPre
            args['language'] = attrs.get('class', '').replace('language-', '') or ''
        elif tag == 'a':
            url = attrs.get('href', '')
            if not url:
                return
            if url.startswith('mailto:'):
                url = url[len('mailto:'):]
                EntityType = MessageEntityEmail
            elif url.startswith('tg://user?id='):
                try:
                    uid = int(url.split('=', 1)[1])
                    EntityType = MessageEntityMentionName
                    args['user_id'] = uid
                    url = None
                except (ValueError, IndexError):
                    EntityType = MessageEntityTextUrl
                    args['url'] = del_surrogate(url)
                    url = None
            else:
                if self.get_starttag_text() == url:
                    EntityType = MessageEntityUrl
                else:
                    EntityType = MessageEntityTextUrl
                    args['url'] = del_surrogate(url)
                    url = None
            self._open_tags_meta.popleft()
            self._open_tags_meta.appendleft(url)
        elif tag == 'tg-emoji':
            try:
                emoji_id = int(attrs.get('emoji-id', ''))
            except (ValueError, KeyError):
                return
            EntityType = MessageEntityCustomEmoji
            args['document_id'] = emoji_id

        if EntityType and tag not in self._building_entities:
            self._building_entities[tag] = EntityType(
                offset=len(self.text),
                length=0,
                **args,
            )

    def handle_data(self, text):
        previous_tag = self._open_tags[0] if self._open_tags else ''
        if previous_tag == 'a':
            url = self._open_tags_meta[0]
            if url:
                text = url

        for entity in self._building_entities.values():
            entity.length += len(text)

        self.text += text

    def handle_endtag(self, tag):
        try:
            self._open_tags.popleft()
            self._open_tags_meta.popleft()
        except IndexError:
            pass
        entity = self._building_entities.pop(tag, None)
        if entity:
            self.entities.append(entity)


# ── Public API ─────────────────────────────────────────────────────────────────

def parse(html: str) -> Tuple[str, List[TypeMessageEntity]]:
    """
    Parse HTML-formatted text into (plain_text, entity_list).

    Supported tags: <b>, <strong>, <i>, <em>, <u>, <s>, <del>, <strike>,
    <code>, <pre>, <a href="...">, <blockquote>, <spoiler>,
    <tg-emoji emoji-id="...">.
    """
    if not html:
        return html, []

    parser = HTMLToTelegramParser()
    parser.feed(add_surrogate(html))
    text = strip_text(parser.text, parser.entities)
    parser.entities.reverse()
    parser.entities.sort(key=lambda e: e.offset)
    return del_surrogate(text), parser.entities


# Entity → (open_tag, close_tag) table
_ENTITY_TO_TAG = {
    MessageEntityBold:       ('<b>', '</b>'),
    MessageEntityItalic:     ('<i>', '</i>'),
    MessageEntityCode:       ('<code>', '</code>'),
    MessageEntityUnderline:  ('<u>', '</u>'),
    MessageEntityStrike:     ('<s>', '</s>'),
    MessageEntityBlockquote: ('<blockquote>', '</blockquote>'),
    MessageEntitySpoiler:    ('<spoiler>', '</spoiler>'),
}


def unparse(text: str, entities) -> str:
    """
    Rebuild HTML from plain *text* and its MessageEntity list.

    This is the reverse of :func:`parse`.
    """
    if not text or not entities:
        return escape(text) if text else text

    from ..tl.tlobject import TLObject
    if isinstance(entities, TLObject):
        entities = (entities,)

    text = add_surrogate(text)
    insert_at = []

    for i, entity in enumerate(entities):
        s = entity.offset
        e = entity.offset + entity.length
        tag_pair = _ENTITY_TO_TAG.get(type(entity))
        if tag_pair:
            open_tag, close_tag = tag_pair
            insert_at.append((s, i, open_tag))
            insert_at.append((e, -i, close_tag))
        elif isinstance(entity, MessageEntityPre):
            lang = getattr(entity, 'language', '') or ''
            if lang:
                insert_at.append((s, i, f"<pre><code class='language-{lang}'>"))
                insert_at.append((e, -i, '</code></pre>'))
            else:
                insert_at.append((s, i, '<pre>'))
                insert_at.append((e, -i, '</pre>'))
        elif isinstance(entity, MessageEntityEmail):
            inner = del_surrogate(text[s:e])
            insert_at.append((s, i, f'<a href="mailto:{inner}">'))
            insert_at.append((e, -i, '</a>'))
        elif isinstance(entity, MessageEntityUrl):
            inner = del_surrogate(text[s:e])
            insert_at.append((s, i, f'<a href="{escape(inner)}">'))
            insert_at.append((e, -i, '</a>'))
        elif isinstance(entity, MessageEntityTextUrl):
            url = escape(entity.url or '')
            insert_at.append((s, i, f'<a href="{url}">'))
            insert_at.append((e, -i, '</a>'))
        elif isinstance(entity, MessageEntityMentionName):
            insert_at.append((s, i, f'<a href="tg://user?id={entity.user_id}">'))
            insert_at.append((e, -i, '</a>'))
        elif isinstance(entity, MessageEntityCustomEmoji):
            insert_at.append((s, i, f'<tg-emoji emoji-id="{entity.document_id}">'))
            insert_at.append((e, -i, '</tg-emoji>'))

    insert_at.sort(key=lambda t: (t[0], t[1]))
    result = []
    last_idx = 0

    while insert_at:
        at, _, tag = insert_at.pop(0)
        while within_surrogate(text, at) if at < len(text) else False:
            at += 1
        result.append(escape(del_surrogate(text[last_idx:at])))
        result.append(tag)
        last_idx = at

    result.append(escape(del_surrogate(text[last_idx:])))
    return ''.join(result)
