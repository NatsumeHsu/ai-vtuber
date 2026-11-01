import random
from collections import defaultdict

from vtuber.rpg.idleology.core.combat.economy.drops import roll_essence_drop
from vtuber.rpg.idleology.core.emojis import (
    BLESSED_BISMUTH,
    CAPRICIOUS_CARP,
    CODEX_TOME_EMOJI,
    ESSENCE_COMMON,
    GOLD_COIN,
    GUILD_TICKET,
    LIFE_ROOT,
    MAGMA_CORE,
    PINNACLE_KEY,
    RUNE_POTENTIAL,
    RUNE_REFINEMENT,
    RUNE_SHATTER,
    SPARKLING_SPRIG,
    SPIRIT_SHARD,
)
from vtuber.rpg.idleology.core.images import CURIO_PUZZLE_BOX

PUZZLE_BOX_IMAGE = CURIO_PUZZLE_BOX

REWARD_POOL = [
    "Potential Runes",
    "Refinement Runes",
    "Shattering Runes",
    "Guild Tickets",
    "Gold",
    "Essences",
    "Elemental Keys",
    "Pinnacle Keys",
    "Antique Tomes",
    "Settler Materials",
]

REWARD_EMOJIS = {
    "Potential Runes": RUNE_POTENTIAL,
    "Refinement Runes": RUNE_REFINEMENT,
    "Shattering Runes": RUNE_SHATTER,
    "Guild Tickets": GUILD_TICKET,
    "Gold": GOLD_COIN,
    "Essences": "🧪",
    "Elemental Keys": "🗝️",
    "Pinnacle Keys": PINNACLE_KEY,
    "Antique Tomes": CODEX_TOME_EMOJI,
    "Settler Materials": "⚒️",
}


def _roll_quantity(rtype: str) -> int:
    if rtype in ("Pinnacle Keys", "Antique Tomes"):
        return random.randint(1, 3)
    if rtype == "Gold":
        return random.randint(1, 10_000_000)
    return random.randint(1, 10)


def format_slot_display(rtype: str, qty: int) -> str:
    if rtype == "Gold":
        return f"{qty:,}g"
    return f"x{qty}"


def roll_slot(exclude: set | None = None) -> tuple[str, int]:
    """Roll a random reward type and quantity, excluding given types."""
    pool = [r for r in REWARD_POOL if not exclude or r not in exclude]
    if not pool:
        pool = REWARD_POOL
    rtype = random.choice(pool)
    return (rtype, _roll_quantity(rtype))


def roll_all_slots() -> list[tuple[str, int]]:
    slots = []
    used: set[str] = set()
    for _ in range(3):
        rtype, qty = roll_slot(exclude=used)
        used.add(rtype)
        slots.append((rtype, qty))
    return slots


async def claim_rewards(
    bot, user_id: str, server_id: str, slots: list[tuple[str, int]]
) -> list[dict]:
    """Grant pre-rolled slot rewards. Returns one display record per slot for the
    result embed: {"emoji": ..., "title": ..., "lines": [...]} — "lines" holds a
    separate, individually-emoji'd entry per reward sub-type (e.g. one line per
    essence type) so multi-type slots don't get flattened into a single run-on line.
    """
    # Local import mirrors core/combat/ui/victory_screen.py — ESSENCE_DISPLAY lives
    # in the essence view module but is already the de-facto shared essence
    # name/emoji lookup used outside of views (core/alchemy/mechanics.py, etc).
    from vtuber.rpg.idleology.core.items.essence_views import ESSENCE_DISPLAY

    results = []

    for rtype, qty in slots:
        emoji = REWARD_EMOJIS[rtype]
        display = format_slot_display(rtype, qty)

        if rtype == "Potential Runes":
            await bot.database.users.modify_currency(user_id, "potential_runes", qty)
            results.append(
                {"emoji": emoji, "title": "Potential Runes", "lines": [display]}
            )

        elif rtype == "Refinement Runes":
            await bot.database.users.modify_currency(user_id, "refinement_runes", qty)
            results.append(
                {"emoji": emoji, "title": "Refinement Runes", "lines": [display]}
            )

        elif rtype == "Shattering Runes":
            await bot.database.users.modify_currency(user_id, "shatter_runes", qty)
            results.append(
                {"emoji": emoji, "title": "Shattering Runes", "lines": [display]}
            )

        elif rtype == "Guild Tickets":
            await bot.database.partners.add_tickets(user_id, qty)
            results.append(
                {"emoji": emoji, "title": "Guild Tickets", "lines": [display]}
            )

        elif rtype == "Gold":
            await bot.database.users.modify_gold(user_id, qty)
            results.append({"emoji": emoji, "title": "Gold", "lines": [display]})

        elif rtype == "Essences":
            essence_counts = defaultdict(int)
            for _ in range(qty):
                etype = roll_essence_drop()
                essence_counts[etype] += 1
                await bot.database.essences.add(user_id, etype)
            lines = []
            for etype, cnt in essence_counts.items():
                name, e_emoji = ESSENCE_DISPLAY.get(
                    etype, (etype.title(), ESSENCE_COMMON)
                )
                lines.append(f"{e_emoji} {name} x{cnt}")
            results.append(
                {"emoji": emoji, "title": f"Essences x{qty}", "lines": lines}
            )

        elif rtype == "Elemental Keys":
            key_counts = defaultdict(int)
            key_meta = {
                "blessed_bismuth": ("Bismuth", BLESSED_BISMUTH),
                "sparkling_sprig": ("Sprig", SPARKLING_SPRIG),
                "capricious_carp": ("Carp", CAPRICIOUS_CARP),
            }
            for _ in range(qty):
                col = random.choice(list(key_meta.keys()))
                key_counts[col] += 1
            for col, cnt in key_counts.items():
                if col == "blessed_bismuth":
                    await bot.database.skills.increment_blessed_bismuth(
                        user_id, server_id, cnt
                    )
                elif col == "sparkling_sprig":
                    await bot.database.skills.increment_sparkling_sprig(
                        user_id, server_id, cnt
                    )
                elif col == "capricious_carp":
                    await bot.database.skills.increment_capricious_carp(
                        user_id, server_id, cnt
                    )
            lines = [
                f"{key_meta[col][1]} {key_meta[col][0]} x{cnt}"
                for col, cnt in key_counts.items()
            ]
            results.append(
                {"emoji": emoji, "title": f"Elemental Keys x{qty}", "lines": lines}
            )

        elif rtype == "Pinnacle Keys":
            await bot.database.users.modify_currency(user_id, "pinnacle_key", qty)
            results.append(
                {"emoji": emoji, "title": "Pinnacle Keys", "lines": [display]}
            )

        elif rtype == "Antique Tomes":
            await bot.database.users.modify_currency(user_id, "antique_tome", qty)
            results.append(
                {"emoji": emoji, "title": "Antique Tomes", "lines": [display]}
            )

        elif rtype == "Settler Materials":
            mat_counts = defaultdict(int)
            mat_meta = {
                "magma_core": ("Magma Core", MAGMA_CORE),
                "life_root": ("Life Root", LIFE_ROOT),
                "spirit_shard": ("Spirit Shard", SPIRIT_SHARD),
            }
            for _ in range(qty):
                col = random.choice(list(mat_meta.keys()))
                mat_counts[col] += 1
            for col, cnt in mat_counts.items():
                await bot.database.settlement_materials.modify(user_id, col, cnt)
            lines = [
                f"{mat_meta[col][1]} {mat_meta[col][0]} x{cnt}"
                for col, cnt in mat_counts.items()
            ]
            results.append(
                {"emoji": emoji, "title": f"Settler Materials x{qty}", "lines": lines}
            )

    await bot.database.users.modify_currency(user_id, "curio_puzzle_boxes", -1)
    return results
