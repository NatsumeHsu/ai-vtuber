"""Presentation is Chinese; game values, user text and Discord IDs are intact."""
import asyncio
import ast
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord
from discord import app_commands
from discord.webhook.async_ import async_context
from vtuber.rpg.localization import (ChineseCatalog, ChineseCommandTranslator, LocalizedModal,
                                      OutputLocalizer, RPG_OUTPUT, rpg_output, LOCALES)
from vtuber.rpg.idleology.core.first_use import TUTORIALS
from vtuber.rpg.idleology.core.character.tutorial import _SCENES
from vtuber.rpg.idleology.core.npc_voices import _VOICES


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = ChineseCatalog()

    def test_core_copy_uses_natural_traditional_chinese(self):
        self.assertEqual(self.catalog.text('Got it!'), '懂了，出發！')
        self.assertIn('能活著回來，才有下次冒險', self.catalog.text(TUTORIALS['combat']['description']))
        self.assertIn('我又不會咬人', self.catalog.text(TUTORIALS['shop']['description']))
        for scene in _SCENES:
            result = self.catalog.text(scene['text'])
            self.assertNotIn('Your ', result)
            self.assertNotEqual(result, scene['text'])

    def test_every_npc_has_editorial_copy(self):
        translations = json.loads((LOCALES / 'editorial.json').read_text(encoding='utf-8'))
        for key, lines in _VOICES.items():
            for line in lines:
                with self.subTest(npc=key, source=line[:30]):
                    self.assertIn(line, translations)
                    self.assertRegex(translations[line], r'[\u4e00-\u9fff]')

    def test_user_names_commands_links_and_metadata_are_preserved(self):
        payload = {
            'content': 'Attack — Gold: 200 /inventory <:gold_coin:123> https://example.com/Attack',
            'embeds': [{'title': 'Inventory', 'description': 'Attack', 'url': 'https://example.com/Attack',
                        'fields': [{'name': 'Gold', 'value': '200'}]}],
            'components': [{'type': 1, 'components': [
                {'type': 2, 'label': 'Attack', 'custom_id': 'Attack'},
                {'type': 3, 'placeholder': 'Choose Your Path', 'custom_id': 'select',
                 'options': [{'label': 'Weapon', 'value': 'weapon', 'description': 'Gold'}]}]}]
        }
        translated = self.catalog.payload(payload, ['Attack'])
        self.assertIn('Attack', translated['content'])
        self.assertIn('/inventory', translated['content'])
        self.assertIn('<:gold_coin:123>', translated['content'])
        self.assertIn('https://example.com/Attack', translated['content'])
        self.assertEqual(translated['embeds'][0]['description'], 'Attack')
        self.assertEqual(translated['embeds'][0]['url'], payload['embeds'][0]['url'])
        button, select = translated['components'][0]['components']
        self.assertEqual(button['label'], '攻擊')
        self.assertEqual(button['custom_id'], 'Attack')
        self.assertEqual(select['options'][0]['label'], '武器')
        self.assertEqual(select['options'][0]['value'], 'weapon')
        self.assertEqual(payload['components'][0]['components'][0]['label'], 'Attack')

    def test_templates_keep_runtime_values_in_the_right_place(self):
        templates = json.loads((LOCALES / 'templates.json').read_text(encoding='utf-8'))
        entry = next(e for e in templates if e['source'] == 'Please wrap up your ZXV000ZXV interaction first.')
        text = entry['source'].replace('ZXV000ZXV', 'private-player-name')
        result = self.catalog.text(text)
        self.assertIn('private-player-name', result)
        self.assertNotIn('Please', result)

    def test_generated_gear_names_use_chinese_word_order(self):
        self.assertEqual(self.catalog.text('Bloodied Sword of the Colossus'), '巨像之染血長劍')
        self.assertEqual(self.catalog.text('Ethereal Mail of the Mountain'), '山岳之虛靈鎖甲')
        original = 'Bloodied Sword of the Colossus'
        self.assertEqual(self.catalog.text(original, protected_names=[original]), original)

    def test_catalog_has_complete_templates_and_no_builder_markers(self):
        entries = json.loads((LOCALES / 'templates.json').read_text(encoding='utf-8'))
        for entry in entries:
            source = entry['source']
            self.assertIn(source, self.catalog.translations)
            target = self.catalog.translations[source]
            self.assertEqual(sorted(re.findall(r'ZXV\d{3}ZXV', source)), sorted(re.findall(r'ZXV\d{3}ZXV', target)))
            self.assertNotRegex(target, r'ZX[QK]\d+ZX[QK]')

    def test_all_story_and_asset_text_is_in_catalog(self):
        root = LOCALES.parent / 'idleology/assets'
        stories = json.loads((root / 'partners/affinity_stories.json').read_text(encoding='utf-8'))
        for chapters in stories.values():
            for story in chapters.values():
                self.assertIn(story['text'], self.catalog.translations)
                self.assertRegex(self.catalog.text(story['text']), r'[\u4e00-\u9fff]')
        self.assertGreater(len(self.catalog.translations), 9000)

    def test_all_upstream_modals_capture_rpg_locale(self):
        for path in (LOCALES.parent / 'idleology').rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        self.assertNotIn(ast.unparse(base), ('Modal', 'ui.Modal', 'discord.ui.Modal'), str(path))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.webhook = SimpleNamespace(request=AsyncMock(return_value={}))
        self.original_context = async_context.set(self.webhook)
        self.addCleanup(async_context.reset, self.original_context)
        self.bot = SimpleNamespace(http=SimpleNamespace(request=AsyncMock(return_value={})))
        self.game = SimpleNamespace(database=SimpleNamespace(users=SimpleNamespace(get_all=AsyncMock(return_value=[
            {'name': 'Attack', 'prestige_display_name': None, 'ideology': 'My Untranslated Creed'}]))))
        self.localizer = OutputLocalizer(self.bot, self.game)
        self.bot.rpg_localizer = self.localizer

    async def test_interaction_webhooks_are_localized_only_inside_rpg(self):
        class Screen:
            def __init__(self, bot):
                self.bot = bot
            @rpg_output
            async def send(self):
                await async_context.get().request('route', payload={'type': 4, 'data': {'content': 'Got it!'}})
                return LocalizedModal(title='Choose Your Path')
        modal = await Screen(self.bot).send()
        payload = self.webhook.request.call_args.kwargs['payload']
        self.assertEqual(payload['data']['content'], '懂了，出發！')
        self.assertIs(modal._rpg_localizer, self.localizer)
        self.assertFalse(RPG_OUTPUT.get())
        self.assertIs(async_context.get(), self.webhook)
        await async_context.get().request('route', payload={'content': 'Got it!'})
        self.assertEqual(self.webhook.request.call_args.kwargs['payload']['content'], 'Got it!')

    async def test_regular_message_edits_and_multipart_preserve_ids(self):
        original = self.localizer.original_request
        token = RPG_OUTPUT.set(self.localizer)
        try:
            await self.localizer.request('edit', json={'content': 'Got it!'})
            self.assertEqual(original.call_args.kwargs['json']['content'], '懂了，出發！')
            form = [{'name': 'payload_json', 'value': json.dumps({'content': 'Got it!'})}]
            await self.localizer.request('edit', form=form)
            self.assertEqual(json.loads(original.call_args.kwargs['form'][0]['value'])['content'], '懂了，出發！')
            self.assertEqual(json.loads(form[0]['value'])['content'], 'Got it!')
        finally:
            RPG_OUTPUT.reset(token)
        await self.localizer.request('non-rpg', json={'content': 'Got it!'})
        self.assertEqual(original.call_args.kwargs['json']['content'], 'Got it!')

    async def test_slash_command_canonical_name_is_unchanged(self):
        @app_commands.command(name='combat', description='Start your journey.')
        async def combat(interaction: discord.Interaction):
            pass
        translator = ChineseCommandTranslator(self.localizer.catalog, {id(combat)})
        tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
        payload = await combat.get_translated_payload(tree, translator)
        self.assertEqual(payload['name'], 'combat')
        self.assertEqual(payload['name_localizations']['zh-TW'], '戰鬥')
        self.assertIn('冒險', payload['description_localizations']['zh-TW'])


if __name__ == '__main__':
    unittest.main()
