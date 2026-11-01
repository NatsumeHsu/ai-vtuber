from pathlib import Path
import aiosqlite

class Bootstrap:
    async def init_db(self) -> None:
        async with aiosqlite.connect(
            str(self.path)
        ) as db:
            with open(
                str(Path(__file__).parent / "database/schema.sql")
            , encoding="utf-8") as file:
                await db.executescript(file.read())
            await db.commit()
            # Idempotent ALTERs to add columns introduced after the base schema.sql
            for stmt in [
                "ALTER TABLE users ADD COLUMN combat_stamina INTEGER NOT NULL DEFAULT 10",
                "ALTER TABLE users ADD COLUMN last_stamina_regen TIMESTAMP DEFAULT NULL",
                # Maw rework: add fights_this_cycle cap tracking.
                "ALTER TABLE maw_participants ADD COLUMN fights_this_cycle INTEGER NOT NULL DEFAULT 0",
                # Maw rework: rename last_damage_check → last_fight_ts (SQLite 3.25+).
                "ALTER TABLE maw_participants RENAME COLUMN last_damage_check TO last_fight_ts",
                "ALTER TABLE uber_shrine_statues ADD COLUMN tier INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE uber_shrine_statues ADD COLUMN slot_index INTEGER NOT NULL DEFAULT 0",
                # Nether Market: expand rotation from 1 to 2 items per tier (lo = below
                # true value, hi = above true value); old single cheap/med/expensive
                # columns are left in place unused.
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_lo_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_lo_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_hi_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_hi_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_lo_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_lo_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_hi_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_hi_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_lo_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_lo_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_hi_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_hi_price INTEGER",
                # Nether Market: expand rotation from 2 to 4 items per tier — two
                # "minor" deviation rolls + two "extreme" deviation rolls; old
                # lo/hi columns above are left in place unused.
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_minor1_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_minor1_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_minor2_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_minor2_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_extreme1_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_extreme1_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_extreme2_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN cheap_extreme2_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_minor1_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_minor1_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_minor2_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_minor2_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_extreme1_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_extreme1_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_extreme2_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN med_extreme2_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_minor1_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_minor1_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_minor2_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_minor2_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_extreme1_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_extreme1_price INTEGER",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_extreme2_item TEXT",
                "ALTER TABLE nether_market_rotation ADD COLUMN expensive_extreme2_price INTEGER",
                # Nether Market: one-shot notice shown next time a plundered victim opens /nether.
                "ALTER TABLE nether_market_profile ADD COLUMN pending_plunder_notice TEXT DEFAULT NULL",
                # Corrupted Monsters / Paradise moved to level 70: new player settings.
                "ALTER TABLE users ADD COLUMN corrupted_encounters_enabled INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE users ADD COLUMN auto_potion_reload INTEGER NOT NULL DEFAULT 0",
                # Prestige rework: emoji "Emblem" cosmetic (replaces title/flair catalogues).
                "ALTER TABLE users ADD COLUMN prestige_emblem TEXT DEFAULT NULL",
                # Rite of Convergence: 5 tradeable entry keys.
                "ALTER TABLE player_currencies ADD COLUMN rite_key_apex_of_dreams INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE player_currencies ADD COLUMN rite_key_corruption_of_memories INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE player_currencies ADD COLUMN rite_key_scales_of_judgment INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE player_currencies ADD COLUMN rite_key_devoid_of_thoughts INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE player_currencies ADD COLUMN rite_key_zenith_of_nightmares INTEGER NOT NULL DEFAULT 0",
                # Rune of Nature retired: respec now costs Rune of Regret, and
                # remnants craft this tradeable Black Market curiosity instead.
                "ALTER TABLE player_currencies ADD COLUMN natures_reprisal INTEGER NOT NULL DEFAULT 0",
                # Elemental of Elements gathering boss: elemental keys were added to
                # schema.sql's CREATE TABLE but never migrated onto existing DBs.
                "ALTER TABLE gathering_mastery ADD COLUMN blessed_bismuth INTEGER DEFAULT 0",
                "ALTER TABLE gathering_mastery ADD COLUMN sparkling_sprig INTEGER DEFAULT 0",
                "ALTER TABLE gathering_mastery ADD COLUMN capricious_carp INTEGER DEFAULT 0",
            ]:
                try:
                    await db.execute(stmt)
                    await db.commit()
                except Exception:
                    pass  # Column already exists

            # One-time data migration: fold the old single-slot player_artefacts
            # row (if any) into the new rite_artefact_items inventory, marked
            # equipped. Idempotent — skips any user/server already present in
            # rite_artefact_items, so this is safe to run on every boot.
            try:
                await db.execute(
                    """INSERT INTO rite_artefact_items
                           (user_id, server_id, artefact_key, roll_1, roll_2, roll_3, is_equipped)
                       SELECT pa.user_id, pa.server_id, pa.artefact_key,
                              pa.roll_1, pa.roll_2, pa.roll_3, 1
                       FROM player_artefacts pa
                       WHERE NOT EXISTS (
                           SELECT 1 FROM rite_artefact_items ri
                           WHERE ri.user_id = pa.user_id AND ri.server_id = pa.server_id
                       )"""
                )
                await db.commit()
            except Exception:
                pass  # Already migrated
