"""
database/repositories/nether_market.py — Nether Market data layer.

Handles three tables:
  nether_market_rotation — the 12 currently active offers (minor1/minor2/extreme1/extreme2
                            per tier), shared per server_id. Legacy lo/hi columns remain in
                            the schema unused, for back-compat with old rows.
  nether_market_holdings — per-player item quantities (item_key -> quantity)
  nether_market_profile  — Nether Marks, mastery nodes_owned, plunder charges, shield/last-plundered
                            timestamps, and a pending one-shot plunder notice
"""

import json
import time

from vtuber.rpg.idleology.database.base import BaseRepository

# Must match core.nether_market.mechanics.OFFER_SLOTS.
_ROTATION_TIERS = ("cheap", "med", "expensive")
_ROTATION_SLOTS = ("minor1", "minor2", "extreme1", "extreme2")
_ROTATION_COLUMNS = [
    f"{tier}_{slot}_{field}"
    for tier in _ROTATION_TIERS
    for slot in _ROTATION_SLOTS
    for field in ("item", "price")
]


class NetherMarketRepository(BaseRepository):
    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------

    async def get_rotation(self, server_id: str) -> dict | None:
        cursor = await self.connection.execute(
            "SELECT * FROM nether_market_rotation WHERE server_id = ?",
            (server_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        keys = [d[0] for d in cursor.description]
        return dict(zip(keys, row))

    async def save_rotation(self, server_id: str, **offers) -> None:
        """`offers` must supply every `{tier}_{slot}_item`/`{tier}_{slot}_price`
        key in _ROTATION_COLUMNS (see NetherMarketMechanics.roll_rotation) —
        callers always pass this via `**rolled`, never explicit kwargs, since
        24 named parameters would be unwieldy by hand."""
        col_list = ", ".join(["server_id", *_ROTATION_COLUMNS, "rotated_at"])
        placeholders = ", ".join(["?"] * (len(_ROTATION_COLUMNS) + 2))
        update_clause = ", ".join(f"{c} = excluded.{c}" for c in _ROTATION_COLUMNS)
        values = (
            [server_id] + [offers[c] for c in _ROTATION_COLUMNS] + [time.time()]
        )
        await self.connection.execute(
            f"""INSERT INTO nether_market_rotation ({col_list})
                 VALUES ({placeholders})
               ON CONFLICT(server_id) DO UPDATE SET
                 {update_clause},
                 rotated_at = excluded.rotated_at""",
            values,
        )
        await self.connection.commit()

    async def get_all_user_ids(self, server_id: str) -> list[str]:
        """Distinct user_ids with a Nether Market profile on this server (i.e. anyone
        who has opened /nether at least once) — used to populate the browse list."""
        cursor = await self.connection.execute(
            "SELECT DISTINCT user_id FROM nether_market_profile WHERE server_id = ?",
            (server_id,),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    # ------------------------------------------------------------------
    # Holdings
    # ------------------------------------------------------------------

    async def get_holdings(self, user_id: str, server_id: str) -> dict:
        """Returns {item_key: quantity} for every item this player currently holds."""
        cursor = await self.connection.execute(
            "SELECT item_key, quantity FROM nether_market_holdings WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_holdings_count(self, user_id: str, server_id: str) -> int:
        """Total item units held (what the 200-slot cap counts against)."""
        cursor = await self.connection.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM nether_market_holdings WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def modify_holdings(
        self, user_id: str, server_id: str, item_key: str, delta: int
    ) -> None:
        """Adjusts quantity by delta (positive or negative), removing the row if it hits 0."""
        await self.connection.execute(
            """INSERT INTO nether_market_holdings (user_id, server_id, item_key, quantity)
               VALUES (?, ?, ?, MAX(0, ?))
               ON CONFLICT(user_id, server_id, item_key) DO UPDATE SET
                 quantity = MAX(0, quantity + ?)""",
            (user_id, server_id, item_key, delta, delta),
        )
        await self.connection.execute(
            "DELETE FROM nether_market_holdings WHERE user_id = ? AND server_id = ? AND quantity <= 0",
            (user_id, server_id),
        )
        await self.connection.commit()

    # ------------------------------------------------------------------
    # Profile (marks, mastery tree, charges, shield)
    # ------------------------------------------------------------------

    async def get_or_create_profile(self, user_id: str, server_id: str) -> dict:
        cursor = await self.connection.execute(
            "SELECT * FROM nether_market_profile WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )
        row = await cursor.fetchone()
        if row:
            keys = [d[0] for d in cursor.description]
            data = dict(zip(keys, row))
            data["mastery_nodes"] = (
                json.loads(data["mastery_nodes"]) if data["mastery_nodes"] else {}
            )
            return data
        await self.connection.execute(
            "INSERT OR IGNORE INTO nether_market_profile (user_id, server_id) VALUES (?, ?)",
            (user_id, server_id),
        )
        await self.connection.commit()
        return {
            "user_id": user_id,
            "server_id": server_id,
            "nether_marks": 0,
            "mastery_nodes": {},
            "plunder_charges": 3,
            "last_charge_time": None,
            "shield_expires_at": None,
            "last_plundered_at": None,
            "pending_plunder_notice": None,
        }

    async def add_marks(self, user_id: str, server_id: str, amount: int) -> None:
        await self.get_or_create_profile(user_id, server_id)
        await self.connection.execute(
            "UPDATE nether_market_profile SET nether_marks = nether_marks + ? "
            "WHERE user_id = ? AND server_id = ?",
            (amount, user_id, server_id),
        )
        await self.connection.commit()

    async def purchase_node(
        self,
        user_id: str,
        server_id: str,
        node_id: str,
        cost: int,
        choice: str | None = None,
    ) -> bool:
        """Atomically deducts Nether Marks and stores the node. Returns True on success."""
        profile = await self.get_or_create_profile(user_id, server_id)
        if profile["nether_marks"] < cost:
            return False
        nodes = profile["mastery_nodes"]
        nodes[node_id] = choice if choice is not None else True
        await self.connection.execute(
            "UPDATE nether_market_profile SET mastery_nodes = ?, nether_marks = nether_marks - ? "
            "WHERE user_id = ? AND server_id = ?",
            (json.dumps(nodes), cost, user_id, server_id),
        )
        await self.connection.commit()
        return True

    # ------------------------------------------------------------------
    # Plunder charges (lazy regen — see core/nether_market/mechanics.py:calculate_charges)
    # ------------------------------------------------------------------

    async def consume_charge(self, user_id: str, server_id: str) -> None:
        await self.get_or_create_profile(user_id, server_id)
        now_ts = time.time()
        await self.connection.execute(
            "UPDATE nether_market_profile SET "
            "  plunder_charges = MAX(0, plunder_charges - 1), "
            "  last_charge_time = COALESCE(last_charge_time, ?) "
            "WHERE user_id = ? AND server_id = ?",
            (now_ts, user_id, server_id),
        )
        await self.connection.commit()

    async def restore_charges(
        self, user_id: str, server_id: str, charges: int, timestamp: float | None
    ) -> None:
        await self.connection.execute(
            "UPDATE nether_market_profile SET plunder_charges = ?, last_charge_time = ? "
            "WHERE user_id = ? AND server_id = ?",
            (charges, timestamp, user_id, server_id),
        )
        await self.connection.commit()

    # ------------------------------------------------------------------
    # Shield / last-plundered tracking
    # ------------------------------------------------------------------

    async def set_shield(self, user_id: str, server_id: str, expires_at: float) -> None:
        await self.get_or_create_profile(user_id, server_id)
        await self.connection.execute(
            "UPDATE nether_market_profile SET shield_expires_at = ? WHERE user_id = ? AND server_id = ?",
            (expires_at, user_id, server_id),
        )
        await self.connection.commit()

    async def is_shielded(self, user_id: str, server_id: str) -> bool:
        profile = await self.get_or_create_profile(user_id, server_id)
        expires_at = profile["shield_expires_at"]
        return expires_at is not None and expires_at > time.time()

    async def record_plunder_time(self, user_id: str, server_id: str) -> None:
        await self.get_or_create_profile(user_id, server_id)
        await self.connection.execute(
            "UPDATE nether_market_profile SET last_plundered_at = ? WHERE user_id = ? AND server_id = ?",
            (time.time(), user_id, server_id),
        )
        await self.connection.commit()

    # ------------------------------------------------------------------
    # Pending plunder notice — shown once, the next time the victim opens /nether
    # ------------------------------------------------------------------

    async def set_plunder_notice(
        self, user_id: str, server_id: str, notice: dict
    ) -> None:
        await self.get_or_create_profile(user_id, server_id)
        await self.connection.execute(
            "UPDATE nether_market_profile SET pending_plunder_notice = ? WHERE user_id = ? AND server_id = ?",
            (json.dumps(notice), user_id, server_id),
        )
        await self.connection.commit()

    async def pop_plunder_notice(self, user_id: str, server_id: str) -> dict | None:
        """Returns the pending plunder notice (if any) and clears it so it only
        ever displays once."""
        cursor = await self.connection.execute(
            "SELECT pending_plunder_notice FROM nether_market_profile WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            return None
        await self.connection.execute(
            "UPDATE nether_market_profile SET pending_plunder_notice = NULL WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )
        await self.connection.commit()
        return json.loads(row[0])
