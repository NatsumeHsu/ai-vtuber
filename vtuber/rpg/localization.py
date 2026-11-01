"""Offline Traditional Chinese presentation; game IDs and saves stay English.

Only Discord text fields are transformed. component custom_id/value, URLs,
attachments, SQL, and player-created names are never translated.
"""
from contextvars import ContextVar
import copy
from functools import wraps
import inspect
import json
from pathlib import Path
import re
from collections import Counter, defaultdict

from discord import app_commands, Locale, ui
from discord.webhook.async_ import AsyncWebhookAdapter, async_context

RPG_OUTPUT = ContextVar('vtuber_rpg_output', default=False)
LOCALES = Path(__file__).parent / 'locales'
TOKEN = re.compile(r'ZXV(\d{3})ZXV')
WORDS = re.compile(r'[A-Za-z]{3,}')
SAFE = re.compile(r'https?://[^\s<>]+|<a?:\w+:\d+>|<[@#][!&]?\d+>|(?<!\w)/[a-z][\w-]*|`[^`\n]*(?:_[^`\n]*|/[^`\n]*)`')


def rpg_output(callback):
    if getattr(callback, '__rpg_output__', False):
        return callback
    @wraps(callback)
    async def wrapped(*args, **kwargs):
        owner = args[0] if args else None
        client = getattr(owner, 'bot', None) or getattr(getattr(owner, 'service', None), 'client', None) or owner
        localizer = getattr(owner, '_rpg_localizer', None) or getattr(client, 'rpg_localizer', None) or RPG_OUTPUT.get()
        token = RPG_OUTPUT.set(localizer or True)
        adapter_token = async_context.set(localizer.webhook_adapter) if isinstance(localizer, OutputLocalizer) else None
        try:
            return await callback(*args, **kwargs)
        finally:
            if adapter_token is not None:
                async_context.reset(adapter_token)
            RPG_OUTPUT.reset(token)
    wrapped.__rpg_output__ = True
    return wrapped


class LocalizedModal(ui.Modal):
    """Capture the creating RPG screen's locale for later modal submissions."""
    def __init__(self, *args, **kwargs):
        self._rpg_localizer = RPG_OUTPUT.get()
        super().__init__(*args, **kwargs)

    @rpg_output
    async def _scheduled_task(self, *args, **kwargs):
        return await super()._scheduled_task(*args, **kwargs)


def instrument_module(module):
    """Scope output from upstream callbacks, background jobs and modal submits."""
    from discord import app_commands, ui
    from discord.ext import tasks
    for value in list(vars(module).values()):
        if not inspect.isclass(value) or not value.__module__.startswith('vtuber.rpg.idleology.'):
            continue
        for name, member in list(vars(value).items()):
            if inspect.iscoroutinefunction(member):
                setattr(value, name, rpg_output(member))
            elif isinstance(member, (staticmethod, classmethod)) and inspect.iscoroutinefunction(member.__func__):
                setattr(value, name, type(member)(rpg_output(member.__func__)))
            elif isinstance(member, app_commands.Command):
                member._callback = rpg_output(member.callback)
            elif isinstance(member, tasks.Loop):
                member.coro = rpg_output(member.coro)
        # View children are captured at class creation. Wrapping dispatch covers
        # those cached button callbacks and all their inherited UI methods.
        if issubclass(value, (ui.View, ui.Modal)):
            value._scheduled_task = rpg_output(value._scheduled_task)
        layout = getattr(ui, 'LayoutView', None)
        if layout and issubclass(value, layout):
            value._scheduled_task = rpg_output(value._scheduled_task)
    for name, value in list(vars(module).items()):
        if inspect.iscoroutinefunction(value) and value.__module__.startswith('vtuber.rpg.idleology.'):
            setattr(module, name, rpg_output(value))


class ChineseCatalog:
    def __init__(self):
        self.translations = json.loads((LOCALES / 'zh_TW.json').read_text(encoding='utf-8'))
        self.translations.update(json.loads((LOCALES / 'glossary.json').read_text(encoding='utf-8')))
        self.translations.update(json.loads((LOCALES / 'editorial.json').read_text(encoding='utf-8')))
        self.equipment = json.loads((LOCALES / 'equipment_zh_TW.json').read_text(encoding='utf-8'))
        for category, entries in self.equipment.items():
            self.translations.update({key: ('「' + value + '」' if category == 'suffixes' else value) for key, value in entries.items()})
        def alternatives(values):
            return '|'.join(re.escape(value) for value in sorted(values, key=len, reverse=True))
        self.gear_pattern = re.compile(r'(?<![A-Za-z])(?P<prefix>' + alternatives(self.equipment['prefixes'])
            + r')\s+(?P<type>' + alternatives(self.equipment['types']) + r')\s+(?P<suffix>'
            + alternatives(self.equipment['suffixes']) + r')(?![A-Za-z])')
        self.translations = {key: value.replace('您', '你').replace('點擊', '點選').replace('這將', '這會')
                             for key, value in self.translations.items()}
        entries = json.loads((LOCALES / 'templates.json').read_text(encoding='utf-8'))
        self.templates = defaultdict(list)
        frequency = Counter(word.lower() for entry in entries for word in WORDS.findall(TOKEN.sub('', entry['source'])))
        for entry in entries:
            source = entry['source']
            target = self.translations.get(source)
            if not target or not TOKEN.search(source) or sorted(TOKEN.findall(source)) != sorted(TOKEN.findall(target)):
                continue
            words = WORDS.findall(TOKEN.sub('', source))
            if not words:
                continue
            anchor = min((word.lower() for word in words), key=lambda w: (frequency[w], -len(w)))
            chunks, position = [], 0
            for match in TOKEN.finditer(source):
                chunks.append(re.escape(source[position:match.start()]))
                chunks.append(f'(?P<p{match[1]}>.*?)')
                position = match.end()
            chunks.append(re.escape(source[position:]))
            self.templates[anchor].append((len(TOKEN.sub('', source)), re.compile(''.join(chunks), re.S), target, entry['slots']))
        self.exact = {source.strip(): target.strip() for source, target in self.translations.items() if not TOKEN.search(source)}
        phrases = {source: target for source, target in self.exact.items()
                   if 2 <= len(source) <= 160 and '\n' not in source and source != target and re.search(r'[A-Za-z]', source)}
        self.phrases = {source.lower(): target for source, target in phrases.items()}
        self.phrase_pattern = re.compile(r'(?<![A-Za-z0-9_])(?:' + '|'.join(re.escape(source) for source in sorted(phrases, key=len, reverse=True)) + r')(?![A-Za-z0-9_])', re.I)

    def _translate(self, text, depth=0):
        if not re.search(r'[A-Za-z]', text):
            return text
        stripped = text.strip()
        if stripped in self.exact:
            return text[:len(text) - len(text.lstrip())] + self.exact[stripped] + text[len(text.rstrip()):]
        candidates = []
        for word in set(WORDS.findall(text.lower())):
            candidates.extend(self.templates.get(word, ()))
        for _, pattern, target, slots in sorted(candidates, key=lambda item: item[0], reverse=True):
            match = pattern.fullmatch(text)
            if not match:
                continue
            values = dict(match.groupdict())
            for key, value in values.items():
                index = int(key[1:])
                hint = slots[index].lower() if index < len(slots) else ''
                # User-authored names and unstructured text stay untouched.
                entity = any(part in hint for part in ('monster', 'weapon', 'item', 'gear', 'equipment', 'partner', 'companion', 'passive', 'building', 'skill', 'resource', 'currency', 'reward', 'modifier', 'status', 'label', 'npc', 'log'))
                if depth < 2 and entity and not any(part in hint for part in ('user', 'player.name', 'self.name', 'input', 'argument', 'ideology', 'nickname')):
                    values[key] = self._translate(value, depth + 1)
            return TOKEN.sub(lambda m: values['p' + m[1]], target)
        if '\n' in text:
            return '\n'.join(self._translate(line, depth) for line in text.split('\n'))
        protected = []
        def hide(match):
            protected.append(match[0])
            return f'\ue002{len(protected) - 1}\ue003'
        text = SAFE.sub(hide, text)
        text = self.gear_pattern.sub(lambda m: self.equipment['suffixes'][m['suffix']] + '之'
                                    + self.equipment['prefixes'][m['prefix']] + self.equipment['types'][m['type']], text)
        text = self.phrase_pattern.sub(lambda m: self.phrases[m[0].lower()], text)
        return re.sub(r'\ue002(\d+)\ue003', lambda m: protected[int(m[1])], text)

    def text(self, value, *, protected_names=()):
        if not isinstance(value, str) or not value:
            return value
        protected = []
        # Non-English sentinels cannot be consumed by the glossary fallback.
        def hide(match):
            protected.append(match[0])
            return f'\ue000{len(protected) - 1}\ue001'
        names = sorted({name for name in protected_names if name}, key=len, reverse=True)
        if names:
            value = re.sub(r'(?<![/A-Za-z0-9_])(?:' + '|'.join(re.escape(name) for name in names) + r')(?![A-Za-z0-9_])', hide, value)
        value = self._translate(value)
        return re.sub(r'\ue000(\d+)\ue001', lambda m: protected[int(m[1])], value)

    def payload(self, payload, protected_names=()):
        result = copy.deepcopy(payload)
        def text(value, protect=True):
            return self.text(value, protected_names=protected_names if protect else ())
        if isinstance(result.get('content'), str):
            result['content'] = text(result['content'])
        for embed in result.get('embeds', []):
            for key in ('title', 'description'):
                if key in embed:
                    embed[key] = text(embed[key])
            for field in embed.get('fields', []):
                field['name'] = text(field.get('name', ''), False)
                field['value'] = text(field.get('value', ''))
            if 'footer' in embed and 'text' in embed['footer']:
                embed['footer']['text'] = text(embed['footer']['text'])
            if 'author' in embed and 'name' in embed['author']:
                embed['author']['name'] = text(embed['author']['name'])
        def component(item):
            for key in ('label', 'description', 'placeholder', 'content', 'title'):
                if key in item and isinstance(item[key], str):
                    item[key] = text(item[key], False)
            for key in ('components', 'items'):
                for child in item.get(key, []):
                    component(child)
            for option in item.get('options', []):
                for key in ('label', 'description'):
                    if key in option:
                        option[key] = text(option[key], False)
            for key in ('component', 'accessory'):
                if isinstance(item.get(key), dict):
                    component(item[key])
        for item in result.get('components', []):
            component(item)
        # Interaction callbacks wrap modal/message fields inside data.
        if isinstance(result.get('data'), dict):
            result['data'] = self.payload(result['data'], protected_names)
            if 'title' in result['data']:
                result['data']['title'] = text(result['data']['title'], False)
        return result


class LocalizedWebhookAdapter(AsyncWebhookAdapter):
    def __init__(self, localizer, original):
        self.localizer, self.original = localizer, original

    async def request(self, route, *args, **kwargs):
        kwargs = await self.localizer.request_payload(kwargs, 'payload', 'multipart')
        # Delegate to the existing adapter, preserving its rate-limit locks.
        return await self.original.request(route, *args, **kwargs)


class OutputLocalizer:
    def __init__(self, bot, game):
        self.bot, self.game, self.catalog = bot, game, ChineseCatalog()
        self.webhook_adapter = LocalizedWebhookAdapter(self, async_context.get())
        self.original_request = bot.http.request

    async def player_names(self):
        rows = await self.game.database.users.get_all()
        return [row[key] for row in rows for key in ('name', 'prestige_display_name', 'ideology') if row[key]]

    async def text_reply(self, text):
        return self.catalog.text(text, protected_names=await self.player_names())

    async def request_payload(self, kwargs, payload_key, form_key):
        names = await self.player_names()
        if isinstance(kwargs.get(payload_key), dict):
            kwargs[payload_key] = self.catalog.payload(kwargs[payload_key], names)
        if kwargs.get(form_key):
            # File fields contain open streams; copy field dictionaries only.
            form = [dict(field) for field in kwargs[form_key]]
            for field in form:
                if field.get('name') == 'payload_json':
                    field['value'] = json.dumps(self.catalog.payload(json.loads(field['value']), names), ensure_ascii=False)
            kwargs[form_key] = form
        return kwargs

    async def request(self, route, **kwargs):
        if RPG_OUTPUT.get() is self:
            kwargs = await self.request_payload(kwargs, 'json', 'form')
        return await self.original_request(route, **kwargs)


class ChineseCommandTranslator(app_commands.Translator):
    def __init__(self, catalog, objects):
        self.catalog, self.objects = catalog, objects
        self.names = json.loads((LOCALES / 'commands.json').read_text(encoding='utf-8'))

    async def translate(self, string, locale, context):
        if locale is not Locale.taiwan_chinese or (id(context.data) not in self.objects and id(getattr(context.data, 'command', None)) not in self.objects):
            return None
        source = string.message
        if context.location in (app_commands.TranslationContextLocation.command_name,
                                app_commands.TranslationContextLocation.group_name):
            return self.names.get(source)
        if context.location is app_commands.TranslationContextLocation.parameter_name:
            return {'name': '名稱', 'option': '選項', 'amount': '數量', 'quantity': '個數', 'target': '目標',
                    'code': '序號', 'stat': '能力', 'url': '網址', 'member': '成員', 'title': '稱號'}.get(source)
        return self.catalog.text(source)


def install_localization(bot, game):
    localizer = OutputLocalizer(bot, game)
    bot.rpg_localizer = game.localizer = localizer
    bot.http.request = localizer.request
    return localizer
