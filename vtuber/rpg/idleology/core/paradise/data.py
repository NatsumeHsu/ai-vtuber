"""
Static definitions for Skill Jewels and Paradise Passives.
All values here are source-of-truth; nothing is hardcoded elsewhere.
"""

from dataclasses import dataclass
from typing import Optional

from vtuber.rpg.idleology.core.emojis import (
    JEWEL_ACRIMONY,
    JEWEL_BASTION,
    JEWEL_CATACLYSM,
    JEWEL_DRAUGHT,
    JEWEL_ONSLAUGHT,
    JEWEL_SIPHON,
    JEWEL_SURGE,
    JEWEL_WARDFORGE,
    PARADISE_JEWEL_UNCUT,
)

# ---------------------------------------------------------------------------
# Skill Jewels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillJewelDef:
    key: str
    name: str
    description_short: str
    # charge trigger description
    charge_trigger: str
    # threshold at level 1, level 20, level 30+
    threshold_lv1: int
    threshold_lv20: int
    threshold_lv30: int
    # unleash description template (use {low}, {high} for damage range)
    unleash_template_lv1: str
    unleash_template_lv20: str
    unleash_template_lv30: str
    emoji: str = f"{PARADISE_JEWEL_UNCUT}"


SKILL_JEWELS: dict[str, SkillJewelDef] = {
    "surge": SkillJewelDef(
        key="surge",
        name="Surge",
        description_short="Lightning storm on repeated hits.",
        charge_trigger="Gain +1 charge on each successful hit.",
        threshold_lv1=16,
        threshold_lv20=10,
        threshold_lv30=8,
        unleash_template_lv1="Lightning storm deals **5–8%** of total ATK as bonus damage.",
        unleash_template_lv20="Lightning storm deals **100–180%** of total ATK as bonus damage.",
        unleash_template_lv30="Lightning storm deals **200–380%** of total ATK as bonus damage.",
        emoji=JEWEL_SURGE,
    ),
    "cataclysm": SkillJewelDef(
        key="cataclysm",
        name="Cataclysm",
        description_short="Prime a devastating guaranteed critical strike.",
        charge_trigger="Gain +1 charge on each critical hit.",
        threshold_lv1=12,
        threshold_lv20=8,
        threshold_lv30=5,
        unleash_template_lv1="Next attack is a guaranteed crit with **+50%** bonus crit multiplier.",
        unleash_template_lv20="Next attack is a guaranteed crit with **+150%** bonus crit multiplier.",
        unleash_template_lv30="Next attack is a guaranteed crit with **+250%** bonus crit multiplier.",
        emoji=JEWEL_CATACLYSM,
    ),
    "acrimony": SkillJewelDef(
        key="acrimony",
        name="Acrimony",
        description_short="Venom burst that poisons on miss.",
        charge_trigger="Gain +1 charge on each missed attack.",
        threshold_lv1=18,
        threshold_lv20=12,
        threshold_lv30=9,
        unleash_template_lv1="Venom burst deals **40–80%** total ATK + 25% of that as DoT for 4 turns.",
        unleash_template_lv20="Venom burst deals **150–250%** total ATK + 25% of that as DoT for 4 turns.",
        unleash_template_lv30="Venom burst deals **300–450%** total ATK + 25% of that as DoT for 4 turns.",
        emoji=JEWEL_ACRIMONY,
    ),
    "wardforge": SkillJewelDef(
        key="wardforge",
        name="Wardforge",
        description_short="Ward burst and damage on ward generation.",
        charge_trigger="Gain +1 charge whenever ward is generated from any source.",
        threshold_lv1=25,
        threshold_lv20=15,
        threshold_lv30=10,
        unleash_template_lv1="Generate **80–150** bonus ward; next attack gains 30% of current ward as bonus damage.",
        unleash_template_lv20="Generate **400–700** bonus ward; next attack gains 30% of current ward as bonus damage.",
        unleash_template_lv30="Generate **800–1200** bonus ward; next attack gains 30% of current ward as bonus damage.",
        emoji=JEWEL_WARDFORGE,
    ),
    "bastion": SkillJewelDef(
        key="bastion",
        name="Bastion",
        description_short="Reflect incoming damage back at the monster.",
        charge_trigger="Gain +1 charge every time you take HP damage.",
        threshold_lv1=12,
        threshold_lv20=9,
        threshold_lv30=5,
        unleash_template_lv1="Reflect **200–400%** of the triggering hit back at the monster.",
        unleash_template_lv20="Reflect **600–1000%** of the triggering hit back at the monster.",
        unleash_template_lv30="Reflect **1200–1800%** of the triggering hit back at the monster.",
        emoji=JEWEL_BASTION,
    ),
    "siphon": SkillJewelDef(
        key="siphon",
        name="Siphon",
        description_short="Burst heal converting HP to ward.",
        charge_trigger="Gain +1 charge whenever HP is regenerated (leech, partner heal, alchemy).",
        threshold_lv1=20,
        threshold_lv20=15,
        threshold_lv30=10,
        unleash_template_lv1="Burst heal **30–60%** of max HP; 50% of heal becomes ward.",
        unleash_template_lv20="Burst heal **40–80%** of max HP; 50% of heal becomes ward.",
        unleash_template_lv30="Burst heal **50–100%** of max HP; 50% of heal becomes ward.",
        emoji=JEWEL_SIPHON,
    ),
    "onslaught": SkillJewelDef(
        key="onslaught",
        name="Onslaught",
        description_short="Massive ATK boost at low HP.",
        charge_trigger="Gain +1 charge each turn while HP is below 50%.",
        threshold_lv1=12,
        threshold_lv20=8,
        threshold_lv30=5,
        unleash_template_lv1="Next attack gains **+60–120%** ATK multiplier.",
        unleash_template_lv20="Next attack gains **+200–350%** ATK multiplier.",
        unleash_template_lv30="Next attack gains **+400–600%** ATK multiplier.",
        emoji=JEWEL_ONSLAUGHT,
    ),
    "draught": SkillJewelDef(
        key="draught",
        name="Draught",
        description_short="Distill potions from combat rhythm.",
        charge_trigger="Gain +1 charge each time a potion is used.",
        threshold_lv1=6,
        threshold_lv20=3,
        threshold_lv30=1,
        unleash_template_lv1="Generates **0–1** potions (overflow converts to ward).",
        unleash_template_lv20="Generates **0–2** potions (overflow converts to ward).",
        unleash_template_lv30="Generates **0–3** potions (overflow converts to ward).",
        emoji=JEWEL_DRAUGHT,
    ),
}


# ---------------------------------------------------------------------------
# Passive Definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PassiveDef:
    key: str
    name: str
    description_template: str  # use {value} for the rolled number
    min_value: float
    max_value: float
    is_percent: bool = True  # whether {value} should be shown as %
    skill_specific: Optional[str] = None  # if set, only applies to this skill key
    flavor: str = ""  # Tessara's one-line in-character gloss on the effect


PASSIVES: dict[str, PassiveDef] = {
    # Charge Manipulation
    "rapid": PassiveDef(
        key="rapid",
        name="Rapid",
        description_template="+{value}% chance to gain a bonus charge every time you gain one at all.",
        min_value=12.0,
        max_value=40.0,
        flavor="The stone doesn't wait for permission — sometimes it just skips ahead.",
    ),
    "compression": PassiveDef(
        key="compression",
        name="Compression",
        description_template="-{value} Charge Threshold on your equipped Skill Jewel (a threshold can never drop below 1).",
        min_value=1.0,
        max_value=4.0,
        is_percent=False,
        flavor="Tightens the space between spark and eruption.",
    ),
    # Unleash Power
    "force": PassiveDef(
        key="force",
        name="Force",
        description_template="+{value}% strength to every unleash. Stacks additively with Fury, Arcane, Sustenance, and Specialization.",
        min_value=15.0,
        max_value=70.0,
        flavor="Raw, indiscriminate power — it doesn't care which skill you channel, it just hits harder.",
    ),
    "mirage": PassiveDef(
        key="mirage",
        name="Mirage",
        description_template="{value}% chance for any unleash to trigger a second time.",
        min_value=6.0,
        max_value=24.0,
        flavor="An echo the Jewel throws back at you, free of charge.",
    ),
    "lingering": PassiveDef(
        key="lingering",
        name="Lingering",
        description_template="{value}% chance to keep 1–5 charges after an unleash instead of resetting to zero.",
        min_value=15.0,
        max_value=50.0,
        flavor="Some of the storm's charge refuses to leave when it should.",
    ),
    # Progression & Mastery
    "savant": PassiveDef(
        key="savant",
        name="Savant",
        description_template="+{value}% faster Skill Jewel leveling.",
        min_value=15.0,
        max_value=60.0,
        flavor="The Jewel remembers this fight even after you've forgotten it.",
    ),
    "mastery": PassiveDef(
        key="mastery",
        name="Mastery",
        description_template="+{value} bonus levels to every Skill Jewel's effective level (capped at level 30).",
        min_value=1.0,
        max_value=4.0,
        is_percent=False,
        flavor="It lends your stone the memory of trials it never actually fought.",
    ),
    # Specific Synergies
    "fury": PassiveDef(
        key="fury",
        name="Fury",
        description_template="+{value}% damage on damage-dealing unleashes (Surge, Acrimony, Bastion).",
        min_value=6.0,
        max_value=25.0,
        flavor="Anger given shape.",
    ),
    "arcane": PassiveDef(
        key="arcane",
        name="Arcane",
        description_template="+{value}% ward generated by jewel effects (Wardforge).",
        min_value=12.0,
        max_value=45.0,
        flavor="Barrier magic layered thick over the stone's pulse.",
    ),
    "sustenance": PassiveDef(
        key="sustenance",
        name="Sustenance",
        description_template="+{value}% healing from jewel effects (Siphon).",
        min_value=15.0,
        max_value=55.0,
        flavor="Life given back with interest.",
    ),
    # Utility
    "fortune": PassiveDef(
        key="fortune",
        name="Fortune",
        description_template="{value}% chance to find a duplicate Paradise Jewel whenever one drops.",
        min_value=1.0,
        max_value=5.0,
        flavor="Luck is just another mineral, if you know where to dig.",
    ),
    # Skill Specialization (rare) — one entry per skill
    "spec_surge": PassiveDef(
        key="spec_surge",
        name="Surge Specialization",
        description_template="+{value}% power to Surge's unleash. Does nothing unless Surge is equipped.",
        min_value=20.0,
        max_value=60.0,
        skill_specific="surge",
        flavor="This facet answers to Surge, and Surge alone.",
    ),
    "spec_cataclysm": PassiveDef(
        key="spec_cataclysm",
        name="Cataclysm Specialization",
        description_template="+{value}% power to Cataclysm's unleash. Does nothing unless Cataclysm is equipped.",
        min_value=20.0,
        max_value=60.0,
        skill_specific="cataclysm",
        flavor="It has eyes only for the killing blow.",
    ),
    "spec_acrimony": PassiveDef(
        key="spec_acrimony",
        name="Acrimony Specialization",
        description_template="+{value}% power to Acrimony's unleash. Does nothing unless Acrimony is equipped.",
        min_value=20.0,
        max_value=60.0,
        skill_specific="acrimony",
        flavor="A vein of venom running straight to one skill.",
    ),
    "spec_wardforge": PassiveDef(
        key="spec_wardforge",
        name="Wardforge Specialization",
        description_template="+{value}% power to Wardforge's unleash. Does nothing unless Wardforge is equipped.",
        min_value=20.0,
        max_value=60.0,
        skill_specific="wardforge",
        flavor="It hums in tune with the ward, and nothing else.",
    ),
    "spec_bastion": PassiveDef(
        key="spec_bastion",
        name="Bastion Specialization",
        description_template="+{value}% power to Bastion's unleash. Does nothing unless Bastion is equipped.",
        min_value=20.0,
        max_value=60.0,
        skill_specific="bastion",
        flavor="Loyal to the reflection, deaf to everything else.",
    ),
    "spec_siphon": PassiveDef(
        key="spec_siphon",
        name="Siphon Specialization",
        description_template="+{value}% power to Siphon's unleash. Does nothing unless Siphon is equipped.",
        min_value=20.0,
        max_value=60.0,
        skill_specific="siphon",
        flavor="It only listens for the sound of healing.",
    ),
    "spec_onslaught": PassiveDef(
        key="spec_onslaught",
        name="Onslaught Specialization",
        description_template="+{value}% power to Onslaught's unleash. Does nothing unless Onslaught is equipped.",
        min_value=20.0,
        max_value=60.0,
        skill_specific="onslaught",
        flavor="It only wakes when you're desperate.",
    ),
    "spec_draught": PassiveDef(
        key="spec_draught",
        name="Draught Specialization",
        description_template="+{value}% power to Draught's unleash. Does nothing unless Draught is equipped.",
        flavor="It steeps quietly, waiting only for Draught.",
        min_value=20.0,
        max_value=60.0,
        skill_specific="draught",
    ),
}

# Passives available in the general roll pool (excludes skill specializations)
GENERAL_PASSIVE_KEYS: list[str] = [
    "rapid",
    "compression",
    "force",
    "mirage",
    "lingering",
    "savant",
    "mastery",
    "fury",
    "arcane",
    "sustenance",
    "fortune",
]
SPEC_PASSIVE_KEYS: list[str] = [k for k in PASSIVES if k.startswith("spec_")]

# Weighted pool: specs are "rare" — appear at 1/4 the rate of general passives
REROLL_TYPE_POOL: list[str] = GENERAL_PASSIVE_KEYS * 4 + SPEC_PASSIVE_KEYS

# Passive slot unlock thresholds per slot index (0-indexed)
PASSIVE_SLOT_THRESHOLDS: list[int] = [1, 4, 9, 19, 34]

# Cosmic Dust costs
DUST_REROLL_TYPE = 12_000
DUST_REROLL_VALUE = 8_000
DUST_FROM_JEWEL_BASE = 8_000


# ---------------------------------------------------------------------------
# Corruption Engram (Evelynn) — per-skill etch effects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngramEffectDef:
    key: str
    kind: str  # "level" | "threshold" | "double_proc"
    value: float
    description: str


# Equal weighting — one entry chosen at random per etch, ~9.1% chance each.
CORRUPTION_ENGRAM_EFFECTS: list[EngramEffectDef] = [
    EngramEffectDef("level_1", "level", 1, "+1 to skill level"),
    EngramEffectDef("level_2", "level", 2, "+2 to skill level"),
    EngramEffectDef("level_3", "level", 3, "+3 to skill level"),
    EngramEffectDef("level_4", "level", 4, "+4 to skill level"),
    EngramEffectDef("level_5", "level", 5, "+5 to skill level"),
    EngramEffectDef("threshold_1", "threshold", 1, "-1 to Charge Threshold"),
    EngramEffectDef("threshold_2", "threshold", 2, "-2 to Charge Threshold"),
    EngramEffectDef("threshold_3", "threshold", 3, "-3 to Charge Threshold"),
    EngramEffectDef(
        "double_5", "double_proc", 5, "+5% chance for unleash to trigger twice"
    ),
    EngramEffectDef(
        "double_10", "double_proc", 10, "+10% chance for unleash to trigger twice"
    ),
    EngramEffectDef(
        "double_15", "double_proc", 15, "+15% chance for unleash to trigger twice"
    ),
]

CORRUPTION_ENGRAM_GOLD_COST = 25_000_000
