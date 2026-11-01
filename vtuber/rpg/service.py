"""Idleology service; all platforms run on the Discord host event loop."""
import asyncio
from contextlib import closing
import hashlib
import logging
from pathlib import Path
import secrets
import sqlite3
import time
from types import SimpleNamespace

import aiosqlite
from vtuber.core.paths import PROJECT_ROOT
from .idleology.bootstrap import Bootstrap
from .idleology.database import DatabaseManager
from .idleology.core.state_manager import StateManager
from .models import Identity


class RPGService(Bootstrap):
    def __init__(self, database='data/rpg.sqlite3', *, world_id, link_ttl_seconds=300, twitch_prefix='!', clock=time.time):
        self.path = Path(database)
        if not self.path.is_absolute():
            self.path = PROJECT_ROOT / self.path
        self.world_id = str(world_id)
        if not self.world_id.isdecimal() or int(self.world_id) <= 0:
            raise ValueError('RPG world_id 必須是 Discord 主伺服器 ID')
        if type(link_ttl_seconds) is not int or not 30 <= link_ttl_seconds <= 3600:
            raise ValueError('link_ttl_seconds 必須為 30–3600 秒')
        self.link_ttl_seconds, self.clock = link_ttl_seconds, clock
        self.twitch_prefix = twitch_prefix
        self.database = None
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger('vtuber.rpg')
        self.state_manager = StateManager(self.logger)
        self.bot = SimpleNamespace(database=None, state_manager=self.state_manager, logger=self.logger)

    async def start(self):
        async with self._lock:
            if self.database is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                with closing(sqlite3.connect(self.path)) as old:
                    prototype = old.execute("SELECT 1 FROM sqlite_master WHERE name='rpg_players'").fetchone()
                    initialized = old.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone()
                    if prototype and not initialized:
                        backup = self.path.with_suffix(self.path.suffix + '.prototype.bak')
                        if not backup.exists():
                            with closing(sqlite3.connect(backup)) as destination:
                                old.backup(destination)
            await self.init_db()
            connection = await aiosqlite.connect(self.path, timeout=30, isolation_level=None)
            try:
                connection.row_factory = sqlite3.Row
                await connection.execute('PRAGMA journal_mode=WAL')
                await connection.execute('PRAGMA busy_timeout=30000')
                database = DatabaseManager(connection=connection)
                await database.quests.create_tables()
                await database.settlement.migrate_buildings_schema()
                await database.settlement.migrate_settlements_schema()
                await database.settlement_materials.migrate_schema()
                await database.paradise.migrate_schema()
                await connection.executescript('''
                    CREATE TABLE IF NOT EXISTS vtuber_rpg_identities (
                        platform TEXT NOT NULL, platform_id TEXT NOT NULL,
                        user_id TEXT NOT NULL, PRIMARY KEY(platform, platform_id));
                    CREATE TABLE IF NOT EXISTS vtuber_rpg_links (
                        code_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE, expires_at REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS vtuber_rpg_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                ''')
                row = await (await connection.execute("SELECT value FROM vtuber_rpg_meta WHERE key='world_id'")).fetchone()
                if row and row['value'] != self.world_id:
                    raise ValueError('RPG 存檔屬於另一個伺服器，請使用不同資料庫路徑')
                await connection.execute("INSERT OR IGNORE INTO vtuber_rpg_meta VALUES ('world_id', ?)", (self.world_id,))
                await connection.commit()
                self.database = self.bot.database = database
            except BaseException:
                await connection.close()
                raise

    async def close(self):
        async with self._lock:
            if self.database:
                await self.database.connection.close()
                self.database = self.bot.database = None

    async def _canonical(self, identity):
        row = await (await self.database.connection.execute(
            'SELECT user_id FROM vtuber_rpg_identities WHERE platform=? AND platform_id=?',
            (identity.platform, identity.user_id))).fetchone()
        # Negative numeric IDs cannot collide with Discord snowflakes and keep
        # upstream leaderboard int(user_id) conversions valid.
        return row['user_id'] if row else (identity.user_id if identity.platform == 'discord' else '-' + identity.user_id)

    async def execute(self, identity: Identity, command: str, name='冒險者') -> str:
        await self.start()
        parts = command.strip().split(maxsplit=1)
        action = parts[0].lower() if parts else 'help'
        argument = parts[1].strip() if len(parts) > 1 else ''
        action = {'說明': 'help', '註冊': 'register', '角色': 'profile', '背包': 'inventory',
                  '戰鬥': 'combat', '綁定': 'link', '配點': 'package'}.get(action, action)
        if action == 'help':
            return ('RPG：register 名稱｜profile｜inventory｜combat（自動戰鬥）｜package 1/2/3（升級配點）｜'
                    'link（Discord 私密取得驗證碼，Twitch 使用 link 驗證碼）。進階玩法請使用 Discord 遊戲指令。')
        async with self._lock:
            uid = await self._canonical(identity)
            if self.state_manager.is_active(uid):
                return '此角色正在進行其他 RPG 操作，請先完成或關閉互動視窗。'
            self.state_manager.set_active(uid, 'shared-command')
            try:
                async with self.database.transaction():
                    if action == 'link':
                        return await self._link(identity, uid, argument)
                    row = await self.database.users.get(uid, self.world_id)
                    if action == 'register':
                        if row:
                            return '你已經有角色，請使用 profile。'
                        return await self._register(uid, argument or name)
                    if not row:
                        return '還沒建立角色呢！先用 register 名稱，準備出發吧。'
                    if action == 'profile':
                        stamina = await self.database.users.get_stamina(uid)
                        return (f'{row["name"]}｜等級 {row["level"]}｜經驗 {row["experience"]}｜金幣 {row["gold"]}｜'
                                f'HP {row["current_hp"]}/{row["max_hp"]}｜體力 {stamina["combat_stamina"]}')
                    if action == 'inventory':
                        counts = []
                        for kind, label in (('weapon', '武器'), ('armor', '護甲'), ('accessory', '飾品'), ('glove', '手套'), ('boot', '鞋子'), ('helmet', '頭盔')):
                            counts.append(f'{label} {await self.database.equipment.get_count(uid, kind)}')
                        return f'{row["name"]} 的背包｜' + '｜'.join(counts) + '。裝備管理請使用 Discord /inventory。'
                    if action == 'package':
                        return await self._package(uid, argument)
                    if action == 'combat':
                        from .combat import auto_combat
                        return await auto_combat(self, uid, row)
                    return '沒認出這個指令耶，用 help 看看能做什麼吧。'
            finally:
                self.state_manager.clear_active(uid)

    async def _register(self, uid, name):
        name = name.strip()
        if not 1 <= len(name) <= 24 or any(ord(c) < 32 for c in name):
            return '角色名稱需為 1–24 字，且不能包含控制字元。'
        from .idleology.core.images import DEFAULT_SILHOUETTE
        ideology = '冒險信念-' + uid
        await self.database.users.register(uid, self.world_id, name, DEFAULT_SILHOUETTE, ideology)
        for skill, tool in (('mining', 'iron'), ('fishing', 'desiccated'), ('woodcutting', 'flimsy')):
            await self.database.skills.initialize(uid, self.world_id, skill, tool)
        await self.database.users.modify_gold(uid, 200)
        await self.database.social.create_ideology(uid, self.world_id, ideology)
        await self.database.social.update_followers(ideology, 1)
        return f'角色「{name}」已建立！先送你 200 金幣準備行囊，用 profile 看看自己的能力吧。'

    async def _link(self, identity, uid, code):
        connection, now = self.database.connection, self.clock()
        await connection.execute('DELETE FROM vtuber_rpg_links WHERE expires_at<=?', (now,))
        if identity.platform == 'discord':
            if code:
                return '請在 Discord 取得驗證碼，再由 Twitch 輸入 link 驗證碼。'
            if not await self.database.users.get(uid, self.world_id):
                return '請先在 Discord 建立角色，再產生綁定碼。'
            token = secrets.token_hex(8)
            await connection.execute('DELETE FROM vtuber_rpg_links WHERE user_id=?', (uid,))
            await connection.execute('INSERT INTO vtuber_rpg_links VALUES (?, ?, ?)',
                                     (hashlib.sha256(token.encode()).hexdigest(), uid, now + self.link_ttl_seconds))
            return f'請在 Twitch 輸入 {self.twitch_prefix}rpg link {token}（{self.link_ttl_seconds} 秒內有效、只能使用一次）。'
        if not code:
            return f'請先在 Discord 使用 /rpg link 取得私密驗證碼，再輸入 {self.twitch_prefix}rpg link 驗證碼。'
        digest = hashlib.sha256(code.encode()).hexdigest()
        link = await (await connection.execute('SELECT user_id FROM vtuber_rpg_links WHERE code_hash=? AND expires_at>?', (digest, now))).fetchone()
        if not link:
            return '綁定碼無效或已過期。'
        if await self.database.users.get(uid, self.world_id) or uid != '-' + identity.user_id:
            return '此 Twitch 帳號已有角色或已綁定，無法直接覆蓋；請使用尚未註冊的帳號綁定。'
        await connection.execute('INSERT INTO vtuber_rpg_identities VALUES (?, ?, ?)', ('twitch', identity.user_id, link['user_id']))
        await connection.execute('DELETE FROM vtuber_rpg_links WHERE code_hash=?', (digest,))
        return '綁定完成，Twitch 與 Discord 現在共用同一個角色與背包。'

    async def _package(self, uid, argument):
        pending = await self.database.users.get_pending_packages(uid, self.world_id)
        if not pending:
            return '目前沒有待選的升級配點。'
        options = pending[0]
        if argument not in ('1', '2', '3'):
            return '請選擇 package 1/2/3：' + '｜'.join(f'{i}: 攻擊 {p["atk"]} 防禦 {p["def"]} HP {p["hp"]}' for i, p in enumerate(options, 1))
        chosen = options[int(argument) - 1]
        for stat, key in (('attack', 'atk'), ('defence', 'def'), ('max_hp', 'hp')):
            await self.database.users.modify_stat(uid, stat, chosen[key])
        await self.database.users.set_pending_packages(uid, self.world_id, pending[1:] or None)
        from .idleology.core.items.factory import load_player
        row = await self.database.users.get(uid, self.world_id)
        player = await load_player(uid, row, self.database)
        await self.database.users.update_hp(uid, player.total_max_hp)
        return f'配點完成：攻擊 +{chosen["atk"]}、防禦 +{chosen["def"]}、HP +{chosen["hp"]}。'
