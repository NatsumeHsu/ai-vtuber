"""Text auto-combat orchestration using Idleology mechanics and rewards."""
import asyncio
import random
from .idleology.core.items.factory import load_player
from .idleology.core.models import Monster
from .idleology.core.combat.mobgen.gen_mob import generate_encounter
from .idleology.core.combat.turns import engine
from .idleology.core.combat.economy.victory import apply_victory_rewards
from .idleology.core.combat.economy.experience import ExperienceManager
from .idleology.core.combat.economy.config import XP_LOSS_ON_DEFEAT
from .idleology.core.combat.turns.boundary import fire_on_victory_effects
from .idleology.core.combat.turns.jewel_engine import save_jewel_state
from .idleology.core.inner_sanctum.mechanics import get_tree_bonuses


async def auto_combat(service, uid, row):
    db, sid = service.database, service.world_id
    if await db.users.get_pending_packages(uid, sid):
        return '升級了！先用 package 1/2/3 選好能力，再繼續冒險吧。'
    await db.users.regen_stamina_tick()
    stamina = await db.users.get_stamina(uid)
    if stamina['combat_stamina'] < 1:
        return '體力見底了，先歇一會兒，恢復後再出發吧。'
    player = await load_player(uid, row, db)
    if player.current_hp <= 1:
        return '傷得有點重，先歇歇吧。等生命恢復，或到 Discord 酒館休息後再戰。'
    tree = await db.inner_sanctum.get(uid, sid)
    saved = random.random() < get_tree_bonuses(tree['nodes_owned'])['stamina_save_chance']
    if not saved:
        await db.users.consume_stamina(uid)
        if stamina['combat_stamina'] >= 10 and stamina['combat_stamina'] - 1 < 10:
            await db.users.set_stamina_regen_time(uid)
    difficulty = await db.users.get_hard_mode(uid)
    streak = await db.users.get_combat_streak(uid)
    monster = await generate_encounter(player, Monster('', 1, 10, 10, 0, 1, 1), False)
    if difficulty:
        mult = [1.0, 2.0, 2.5, 3.0, 4.0][difficulty]
        monster.bonus_attack_pct += mult - 1
        monster.bonus_defence_pct += mult - 1
        monster.hp = monster.max_hp = int(monster.hp * 1.5)
        monster.difficulty_level = difficulty
        monster.difficulty_dr = [0, 0, 0, .10, .25][difficulty]
        monster.attack = int(monster.base_attack * (1 + monster.bonus_attack_pct))
        monster.defence = int(monster.base_defence * (1 + monster.bonus_defence_pct))
    monster.reset_combat_bonuses()
    engine.apply_stat_effects(player, monster)
    engine.apply_combat_start_passives(player, monster)
    rounds = 0
    while monster.hp > 0 and player.current_hp > 0 and rounds < 600:
        engine.process_player_turn(player, monster)
        if monster.hp > 0:
            engine.process_monster_turn(player, monster)
        rounds += 1
        if rounds % 20 == 0:
            await asyncio.sleep(0)
    if monster.hp <= 0 and player.current_hp > 0:
        rewards = await apply_victory_rewards(service.bot, uid, sid, player, monster, None, None)
        bonus_pct = min(50, streak // 10) + [0, 50, 75, 100, 150][difficulty]
        bonus_xp, bonus_gold = int(rewards['xp'] * bonus_pct / 100), int(rewards['gold'] * bonus_pct / 100)
        if bonus_xp:
            changes = await ExperienceManager.add_experience(service.bot, uid, player, bonus_xp, server_id=sid)
            rewards['xp'] += changes['xp_added']
        if bonus_gold:
            await db.users.modify_gold(uid, bonus_gold)
            rewards['gold'] += bonus_gold
        result = f'戰勝 {monster.name}！{rounds} 回合｜金幣 +{rewards["gold"]}｜經驗 +{rewards["xp"]}'
        await db.users.increment_combat_streak(uid)
        fire_on_victory_effects(player)
    elif player.current_hp <= 0:
        protected = await db.users.get_exp_protection(uid)
        if difficulty and not protected:
            loss, player.exp = player.exp, 0
        else:
            loss = await ExperienceManager.remove_experience(service.bot, uid, player, int(player.exp * XP_LOSS_ON_DEFEAT))
        player.current_hp = 1
        await db.users.reset_combat_streak(uid)
        result = f'敗給 {monster.name}，經驗損失 {loss}。'
    else:
        result = f'與 {monster.name} 戰鬥達到 600 回合，已撤退。'
    await db.users.update_from_player_object(player)
    await save_jewel_state(service.bot, uid, player)
    return result + f'｜HP {player.current_hp}/{player.total_max_hp}。'
