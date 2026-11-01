"""
Artisan Mastery UI

- Hub (ArtisanMasteryHubView): 3 skill buttons + Nature's Attunement + Crafting + Back.
- Skill view (SkillMasteryView): Shows a summary of all 3 branches, navigates to each,
  and offers a direct Respec (costs Rune of Regret).
- Branch view (BranchDetailView): Shows one branch in full detail with Invest + Back buttons.
- Crafting view (CraftingView): Turns Geode Cores/Tide Relics/Heartwood Shards into
  Nature's Reprisal (tradeable at the Black Market) — hub-level, not skill-specific.
"""

import json

import discord
from discord import ButtonStyle, Interaction
from discord.ui import Button

from vtuber.rpg.idleology.core.base_view import BaseView
from vtuber.rpg.idleology.core.images import (
    ARTISAN_MASTERY_ATTUNEMENT,
    ARTISAN_MASTERY_HUB,
    MASTERY_FISHING,
    MASTERY_MINING,
    MASTERY_WOODCUTTING,
)
from vtuber.rpg.idleology.core.skills.mastery import (
    BRANCH_NODE_ORDERS,
    NATURE_ATTUNEMENT_NODE_ORDER,
    NATURE_ATTUNEMENT_TREE,
    NATURES_REPRISAL_CRAFT_COST,
    NODE_LABELS,
    get_attunement_progress,
    get_branch_display_name,
    get_branch_progress,
    get_branch_total_cost,
    get_prestige_spawn_bonus,
    get_rich_base_bonus,
    get_total_insight_bonuses,
    get_tree,
    get_yield_proc_bonus,
    has_nature_attunement_unlocked,
    invest_in_attunement,
    invest_in_branch,
)
from vtuber.rpg.idleology.core.skills.mechanics import SkillMechanics

# =========================================================
# HUB VIEW
# =========================================================


class ArtisanMasteryHubView(BaseView):
    """Entry point for Artisan Mastery. 3 skill buttons + Attunement + Crafting + Back."""

    def __init__(
        self, bot, user_id: str, server_id: str, parent_view: BaseView | None = None
    ):
        super().__init__(bot, user_id, server_id, parent=parent_view)
        self.parent_view = parent_view
        self._processing = False

    async def refresh(self):
        self.mastery_row = await self.bot.database.skills.get_mastery(
            self.user_id, self.server_id
        )
        self.setup_ui()

    def setup_ui(self):
        self.clear_items()

        for s in ["mining", "woodcutting", "fishing"]:
            info = SkillMechanics.get_skill_info(s)
            btn = Button(
                label=info["display_name"],
                emoji=info["emoji"],
                style=ButtonStyle.primary,
                row=0,
            )
            btn.callback = self._make_skill_button_cb(s)
            self.add_item(btn)

        gate_met = has_nature_attunement_unlocked(self.mastery_row or {})
        att_btn = Button(
            label="Nature's Attunement",
            emoji="🌿",
            style=ButtonStyle.success if gate_met else ButtonStyle.secondary,
            row=1,
            disabled=not gate_met,
        )
        att_btn.callback = self._open_attunement_cb
        self.add_item(att_btn)

        craft_btn = Button(
            label="Crafting",
            emoji="🛠️",
            style=ButtonStyle.primary,
            row=1,
        )
        craft_btn.callback = self._open_crafting_cb
        self.add_item(craft_btn)

        back_btn = Button(label="Back to Gather", style=ButtonStyle.secondary, row=2)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    async def _open_attunement_cb(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()
        view = AttunementView(self.bot, self.user_id, self.server_id, parent_view=self)
        await view.refresh()
        await interaction.edit_original_response(embed=view.get_embed(), view=view)
        view.message = await interaction.original_response()
        self._processing = False

    async def _open_crafting_cb(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()
        view = CraftingView(self.bot, self.user_id, self.server_id, parent_view=self)
        await view.refresh()
        await interaction.edit_original_response(embed=view.get_embed(), view=view)
        view.message = await interaction.original_response()
        self._processing = False

    def _make_skill_button_cb(self, skill: str):
        async def cb(interaction: Interaction):
            if self._processing:
                await interaction.response.defer()
                return
            self._processing = True
            await interaction.response.defer()
            view = SkillMasteryView(
                self.bot, self.user_id, self.server_id, skill, parent_view=self
            )
            await view.refresh()
            await interaction.edit_original_response(embed=view.get_embed(), view=view)
            view.message = await interaction.original_response()
            self._processing = False

        return cb

    async def back_callback(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()
        if self.parent_view:
            self.parent_view._processing = False
            await self.parent_view.refresh_state()
            await interaction.edit_original_response(
                embed=self.parent_view.get_embed(), view=self.parent_view
            )
        else:
            await interaction.delete_original_response()
            self.bot.state_manager.clear_active(self.user_id)
        self.stop()

    def get_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Artisan Mastery",
            description=(
                "Choose a gathering skill to view its specialization trees and invest Artisan Points.\n\n"
                "🌿 **Nature's Attunement** unlocks after investing **20+ points** in each of the three skills."
            ),
            color=0x2E8B57,
        )
        embed.set_thumbnail(url=ARTISAN_MASTERY_HUB)
        embed.set_footer(
            text="The Black Market is seeking rare gathering materials for trade."
        )
        return embed


# =========================================================
# SKILL-SPECIFIC VIEW — summary of all 3 branches
# =========================================================


class SkillMasteryView(BaseView):
    """Summary view for one skill. Navigate into each branch for details and investing,
    or respec the skill's entire tree directly using Rune of Regret."""

    RESPEC_COST = 1  # Runes of Regret

    def __init__(
        self,
        bot,
        user_id: str,
        server_id: str,
        skill: str,
        parent_view: BaseView | None = None,
    ):
        super().__init__(bot, user_id, server_id, parent=parent_view)
        self.parent_view = parent_view
        self.skill = skill
        self._processing = False
        self.mastery_row = None
        self.rune_of_regret = 0

    async def refresh(self):
        self.mastery_row = await self.bot.database.skills.get_mastery(
            self.user_id, self.server_id
        )
        self.rune_of_regret = await self.bot.database.users.get_currency(
            self.user_id, "rune_of_regret"
        )
        self.setup_ui()

    def _safe_get(self, row, key, default=0):
        if row is None:
            return default
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return default

    def setup_ui(self):
        self.clear_items()

        for branch in ["yield", "quality", "synergy"]:
            btn = Button(
                label=f"View {get_branch_display_name(branch)}",
                style=ButtonStyle.primary,
                row=0,
            )
            btn.callback = self._make_branch_cb(branch)
            self.add_item(btn)

        can_respec = self.rune_of_regret >= self.RESPEC_COST
        respec_btn = Button(
            label=f"Respec This Skill ({self.rune_of_regret} Runes of Regret)",
            style=ButtonStyle.danger if can_respec else ButtonStyle.secondary,
            disabled=not can_respec,
            row=1,
        )
        respec_btn.callback = self.confirm_respec_callback
        self.add_item(respec_btn)

        back_btn = Button(
            label="Back to Mastery Hub", style=ButtonStyle.secondary, row=1
        )
        back_btn.callback = self.back_to_hub_callback
        self.add_item(back_btn)

    def _make_branch_cb(self, branch: str):
        async def cb(interaction: Interaction):
            if self._processing:
                await interaction.response.defer()
                return
            self._processing = True
            await interaction.response.defer()
            view = BranchDetailView(
                self.bot,
                self.user_id,
                self.server_id,
                self.skill,
                branch,
                parent_view=self,
            )
            await view.refresh()
            await interaction.edit_original_response(embed=view.get_embed(), view=view)
            view.message = await interaction.original_response()
            self._processing = False

        return cb

    async def back_to_hub_callback(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()
        if self.parent_view:
            await self.parent_view.refresh()
            await interaction.edit_original_response(
                embed=(
                    self.parent_view.get_embed()
                    if hasattr(self.parent_view, "get_embed")
                    else None
                ),
                view=self.parent_view,
            )
        else:
            await interaction.delete_original_response()
        self.stop()

    async def confirm_respec_callback(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()

        skill_info = SkillMechanics.get_skill_info(self.skill)
        view = RespecConfirmView(self.bot, self.user_id, self.server_id, self)
        embed = discord.Embed(
            title=f"⚠️ Respec {skill_info['display_name']} Mastery?",
            description=(
                f"This will reset **all** invested nodes across the Yield, Quality, "
                f"and Synergy branches of {skill_info['display_name']} Artisan Mastery "
                f"and refund the points spent.\n\n"
                f"**Cost:** {self.RESPEC_COST} Rune of Regret "
                f"(you have **{self.rune_of_regret}**)\n\n"
                "This cannot be undone."
            ),
            color=discord.Color.red(),
        )
        await interaction.edit_original_response(embed=embed, view=view)
        view.message = await interaction.original_response()
        self._processing = False

    async def respec_callback(self, interaction: Interaction):
        """Executes the respec. Called only from RespecConfirmView's Confirm button."""
        spent_rune = await self.bot.database.users.deduct_currency_atomic(
            self.user_id, "rune_of_regret", self.RESPEC_COST
        )
        if not spent_rune:
            await interaction.followup.send(
                f"You need at least {self.RESPEC_COST} Rune of Regret to respec a skill.",
                ephemeral=True,
            )
            self.setup_ui()
            await interaction.edit_original_response(embed=self.get_embed(), view=self)
            return

        alloc = json.loads(
            self._safe_get(self.mastery_row, f"{self.skill}_alloc", "{}") or "{}"
        )
        spent = 0
        tree = get_tree(self.skill)
        for bdata in alloc.values():
            if isinstance(bdata, dict):
                for nk in bdata.get("unlocked", []):
                    if nk in tree:
                        spent += tree[nk].get("cost", 0)

        await self.bot.database.skills.respec_mastery(
            self.user_id, self.server_id, self.skill, spent
        )

        await self.refresh()
        await interaction.edit_original_response(embed=self.get_embed(), view=self)
        skill_info = SkillMechanics.get_skill_info(self.skill)
        await interaction.followup.send(
            f"Respec complete for **{skill_info['display_name']}**. You recovered **{spent}** points.",
            ephemeral=True,
        )

    def get_embed(self) -> discord.Embed:
        info = SkillMechanics.get_skill_info(self.skill)
        m = self.mastery_row or {}
        pts = self._safe_get(m, f"{self.skill}_points", 0)

        skill_img = {
            "mining": MASTERY_MINING,
            "fishing": MASTERY_FISHING,
            "woodcutting": MASTERY_WOODCUTTING,
        }.get(self.skill)

        alloc = json.loads(m.get(f"{self.skill}_alloc", "{}") or "{}")
        lines = []
        for branch in ["yield", "quality", "synergy"]:
            progress = get_branch_progress(self.skill, branch, alloc)
            order = BRANCH_NODE_ORDERS.get(self.skill, {}).get(branch, [])
            total_nodes = len(order)
            invested = progress["invested"]
            nodes_unlocked = len(progress["unlocked"])
            total_cost = get_branch_total_cost(self.skill, branch)
            bonus_inv = progress.get("bonus_invested", 0)
            is_complete = progress.get("complete") and bonus_inv >= 10

            if is_complete:
                status = "✅ Maxed"
            elif nodes_unlocked > 0:
                status = (
                    f"{nodes_unlocked}/{total_nodes} nodes · {invested} pts invested"
                )
            else:
                status = "Not started"

            bonus_txt = (
                f" · Bonus {bonus_inv}/10"
                if invested >= total_cost and not is_complete
                else ""
            )
            lines.append(f"**{get_branch_display_name(branch)}:** {status}{bonus_txt}")

        desc = (
            f"Unspent Artisan Points: **{pts}**\n\n"
            + "\n".join(lines)
            + "\n\nSelect a branch to view its nodes and invest."
        )
        embed = discord.Embed(
            title=f"{info['display_name']} Mastery", description=desc, color=0x2E8B57
        )
        if skill_img:
            embed.set_thumbnail(url=skill_img)
        embed.set_footer(
            text="Nodes unlock automatically as you invest points into a branch."
        )
        return embed


class RespecConfirmView(BaseView):
    """Confirmation gate before an Artisan Mastery respec — irreversible and costs
    a Rune of Regret, so it must not fire directly off the hub button."""

    def __init__(
        self,
        bot,
        user_id: str,
        server_id: str,
        skill_view: "SkillMasteryView",
    ):
        super().__init__(bot, user_id, server_id, parent=skill_view)
        self.skill_view = skill_view
        self._processing = False

        confirm_btn = Button(
            label="Confirm Respec", style=ButtonStyle.danger, row=0
        )
        confirm_btn.callback = self._on_confirm
        self.add_item(confirm_btn)

        cancel_btn = Button(label="Cancel", style=ButtonStyle.secondary, row=0)
        cancel_btn.callback = self._on_cancel
        self.add_item(cancel_btn)

    async def _on_confirm(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()
        await self.skill_view.respec_callback(interaction)
        self._processing = False
        self.stop()

    async def _on_cancel(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()
        self.skill_view.setup_ui()
        await interaction.edit_original_response(
            embed=self.skill_view.get_embed(), view=self.skill_view
        )
        self._processing = False
        self.stop()


# =========================================================
# BRANCH DETAIL VIEW — one branch with full node info
# =========================================================


class BranchDetailView(BaseView):
    """Dedicated view for one mastery branch. Shows all nodes with clean descriptions."""

    def __init__(
        self,
        bot,
        user_id: str,
        server_id: str,
        skill: str,
        branch: str,
        parent_view: BaseView | None = None,
    ):
        super().__init__(bot, user_id, server_id, parent=parent_view)
        self.parent_view = parent_view
        self.skill = skill
        self.branch = branch
        self._processing = False
        self.mastery_row = None
        self.user_row = None

    async def refresh(self):
        self.mastery_row = await self.bot.database.skills.get_mastery(
            self.user_id, self.server_id
        )
        self.user_row = await self.bot.database.users.get(self.user_id, self.server_id)
        self.setup_ui()

    def _safe_get(self, row, key, default=0):
        if row is None:
            return default
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return default

    def setup_ui(self):
        self.clear_items()

        pts = self._safe_get(self.mastery_row, f"{self.skill}_points", 0)
        alloc_json = (
            self._safe_get(self.mastery_row, f"{self.skill}_alloc", "{}") or "{}"
        )
        alloc = json.loads(alloc_json)
        progress = get_branch_progress(self.skill, self.branch, alloc)
        bonus_inv = progress.get("bonus_invested", 0)
        is_complete = progress.get("complete") and bonus_inv >= 10
        can_invest = pts > 0 and not is_complete

        invest_btn = Button(
            label="Invest 1 Point",
            style=ButtonStyle.success if can_invest else ButtonStyle.secondary,
            disabled=not can_invest,
            row=0,
        )
        invest_btn.callback = self._invest_callback
        self.add_item(invest_btn)

        back_btn = Button(label="← Back", style=ButtonStyle.secondary, row=0)
        back_btn.callback = self._back_callback
        self.add_item(back_btn)

    async def _invest_callback(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()

        pts = self._safe_get(self.mastery_row, f"{self.skill}_points", 0)
        alloc_json = (
            self._safe_get(self.mastery_row, f"{self.skill}_alloc", "{}") or "{}"
        )

        if pts < 1:
            await interaction.followup.send(
                "You have no unspent Artisan Points to invest.", ephemeral=True
            )
            self._processing = False
            return

        new_alloc_json, spent, newly_unlocked = invest_in_branch(
            self.skill, self.branch, alloc_json, pts, amount=1
        )
        if spent <= 0:
            await interaction.followup.send(
                "Could not invest (branch may be complete).", ephemeral=True
            )
            self._processing = False
            return

        new_total = (
            self._safe_get(self.mastery_row, "total_mastery_invested", 0) + spent
        )
        await self.bot.database.skills.update_mastery_alloc(
            self.user_id, self.server_id, self.skill, new_alloc_json, new_total
        )
        await self.bot.database.skills.deduct_mastery_points(
            self.user_id, self.server_id, self.skill, spent
        )

        await self.refresh()
        await interaction.edit_original_response(embed=self.get_embed(), view=self)
        self._processing = False

    async def _back_callback(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()
        if self.parent_view:
            await self.parent_view.refresh()
            await interaction.edit_original_response(
                embed=(
                    self.parent_view.get_embed()
                    if hasattr(self.parent_view, "get_embed")
                    else None
                ),
                view=self.parent_view,
            )
        else:
            await interaction.delete_original_response()
        self.stop()

    def get_embed(self) -> discord.Embed:
        info = SkillMechanics.get_skill_info(self.skill)
        m = self.mastery_row or {}
        pts = self._safe_get(m, f"{self.skill}_points", 0)

        alloc = json.loads(m.get(f"{self.skill}_alloc", "{}") or "{}")
        progress = get_branch_progress(self.skill, self.branch, alloc)
        unlocked = set(progress["unlocked"])
        invested = progress["invested"]

        tree = get_tree(self.skill)
        order = BRANCH_NODE_ORDERS.get(self.skill, {}).get(self.branch, [])
        branch_name = get_branch_display_name(self.branch)

        lines = [f"Unspent Points: **{pts}**  ·  Invested in branch: **{invested}**\n"]

        for node_key in order:
            node = tree.get(node_key, {})
            label = NODE_LABELS.get(node_key, node_key)
            desc = node.get("desc", "")
            cost = node.get("cost", 1)
            status = "✅" if node_key in unlocked else "🔒"
            lines.append(f"{status} **{label}** *({cost} pt)*\n{desc}")

        # Bonus investment section
        total_cost = get_branch_total_cost(self.skill, self.branch)
        bonus_inv = progress.get("bonus_invested", 0)
        if invested >= total_cost:
            lines.append(f"\n**Bonus Investment: {bonus_inv}/10**")
            if self.branch == "yield":
                current_pct = get_yield_proc_bonus(self.skill, m) * 100
                lines.append(
                    f"Each bonus point adds +0.4% to the proc chance of the last Yield node. (Current: +{current_pct:.1f}%, max +4.0%)"
                )
            elif self.branch == "quality":
                current_pct = get_rich_base_bonus(self.skill, m) * 100
                lines.append(
                    f"Each bonus point adds +0.5% to the Rich event base chance. (Current: +{current_pct:.1f}%, max +5.0%)"
                )
            elif self.branch == "synergy":
                current_pct = get_prestige_spawn_bonus(self.skill, m) * 100
                lines.append(
                    f"Each bonus point adds +0.2% to the Prestige boss spawn chance. (Current: +{current_pct:.1f}%, max +2.0%)"
                )
        elif progress.get("next_node") and progress["next_node"] != "bonus":
            next_label = NODE_LABELS.get(progress["next_node"], progress["next_node"])
            lines.append(
                f"\n→ Next unlock: **{next_label}** — {progress['next_cost']} more pt(s) needed"
            )

        embed = discord.Embed(
            title=f"{info['display_name']} — {branch_name}",
            description="\n".join(lines),
            color=0x2E8B57,
        )
        skill_img = {
            "mining": MASTERY_MINING,
            "fishing": MASTERY_FISHING,
            "woodcutting": MASTERY_WOODCUTTING,
        }.get(self.skill)
        if skill_img:
            embed.set_thumbnail(url=skill_img)
        embed.set_footer(text="Click 'Invest 1 Point' to advance this branch.")
        return embed


# =========================================================
# CRAFTING VIEW — hub-level, turns remnants into Nature's Reprisal
# =========================================================


class CraftingView(BaseView):
    """Turns Geode Cores/Tide Relics/Heartwood Shards into Nature's Reprisal,
    a Black-Market-tradeable material. Hub-level — not tied to any one skill."""

    def __init__(
        self, bot, user_id: str, server_id: str, parent_view: BaseView | None = None
    ):
        super().__init__(bot, user_id, server_id, parent=parent_view)
        self.parent_view = parent_view
        self._processing = False
        self.mastery_row = None
        self.owned_reprisal = 0
        self._session_crafted = 0

    async def refresh(self):
        self.mastery_row = await self.bot.database.skills.get_mastery(
            self.user_id, self.server_id
        )
        self.owned_reprisal = int(
            await self.bot.database.users.get_currency(
                self.user_id, "natures_reprisal"
            )
        )
        self.setup_ui()

    def _safe_get(self, row, key, default=0):
        if row is None:
            return default
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return default

    def _has_enough_remnants(self) -> bool:
        m = self.mastery_row or {}
        cost = NATURES_REPRISAL_CRAFT_COST
        return all(
            self._safe_get(m, k, 0) >= v for k, v in cost.items()
        )

    def setup_ui(self):
        self.clear_items()

        can_craft = self._has_enough_remnants()
        craft_btn = Button(
            label="Craft Nature's Reprisal",
            style=ButtonStyle.success if can_craft else ButtonStyle.secondary,
            disabled=not can_craft,
            row=0,
        )
        craft_btn.callback = self.craft_callback
        self.add_item(craft_btn)

        back_btn = Button(
            label="Back to Mastery Hub", style=ButtonStyle.secondary, row=1
        )
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    async def craft_callback(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()

        if not self._has_enough_remnants():
            await interaction.followup.send(
                "Not enough remnants (need 50 of each type).", ephemeral=True
            )
            self._processing = False
            return

        cost = NATURES_REPRISAL_CRAFT_COST
        await self.bot.database.skills.modify_remnants(
            self.user_id, self.server_id, {k: -v for k, v in cost.items()}
        )
        await self.bot.database.users.modify_currency(
            self.user_id, "natures_reprisal", 1
        )
        self._session_crafted += 1

        await self.refresh()
        await interaction.edit_original_response(embed=self.get_embed(), view=self)
        self._processing = False

    async def back_callback(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()
        if self.parent_view:
            await self.parent_view.refresh()
            await interaction.edit_original_response(
                embed=(
                    self.parent_view.get_embed()
                    if hasattr(self.parent_view, "get_embed")
                    else None
                ),
                view=self.parent_view,
            )
        else:
            await interaction.delete_original_response()
        self.stop()

    def get_embed(self) -> discord.Embed:
        m = self.mastery_row or {}
        geodes = self._safe_get(m, "geode_cores", 0)
        tides = self._safe_get(m, "tide_relics", 0)
        heartwood = self._safe_get(m, "heartwood_shards", 0)
        cost = NATURES_REPRISAL_CRAFT_COST

        desc = (
            f"**Your Materials:**\n"
            f"• Geode Cores: **{geodes}** / {cost['geode_cores']}\n"
            f"• Tide Relics: **{tides}** / {cost['tide_relics']}\n"
            f"• Heartwood Shards: **{heartwood}** / {cost['heartwood_shards']}\n\n"
            f"**Crafting Cost (per Nature's Reprisal):**\n"
            f"• {cost['geode_cores']} Geode Cores, {cost['tide_relics']} Tide Relics, "
            f"{cost['heartwood_shards']} Heartwood Shards\n\n"
            "Nature's Reprisal is a rare curiosity tradeable at the Settlement's "
            "Black Market — no gold or Spirit Stones required to craft it."
        )

        if self._session_crafted > 0:
            desc = (
                f"✨ **Crafted {self._session_crafted} Nature's Reprisal this visit!**\n\n"
                + desc
            )

        embed = discord.Embed(
            title="Artisan Crafting",
            description=desc,
            color=discord.Color.green() if self._session_crafted > 0 else 0x8A2BE2,
        )
        embed.add_field(
            name="Nature's Reprisal Owned",
            value=f"**{self.owned_reprisal:,}**",
            inline=True,
        )
        embed.set_footer(text="Gathering skills produce remnants via Rich events.")
        embed.set_thumbnail(url=ARTISAN_MASTERY_HUB)
        return embed


# =========================================================
# NATURE'S ATTUNEMENT VIEW
# =========================================================


class AttunementView(BaseView):
    """Cross-skill free investment tree. Unlocks after 20pts invested in each main tree."""

    def __init__(
        self, bot, user_id: str, server_id: str, parent_view: BaseView | None = None
    ):
        super().__init__(bot, user_id, server_id, parent=parent_view)
        self.parent_view = parent_view
        self._processing = False
        self.mastery_row = None
        self.user_row = None

    async def refresh(self):
        self.mastery_row = await self.bot.database.skills.get_mastery(
            self.user_id, self.server_id
        )
        self.user_row = await self.bot.database.users.get(self.user_id, self.server_id)
        self.setup_ui()

    def _safe_get(self, row, key, default=0):
        if row is None:
            return default
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return default

    def setup_ui(self):
        self.clear_items()

        gate_met = has_nature_attunement_unlocked(self.mastery_row or {})
        att_progress = get_attunement_progress(
            self.mastery_row.get("attunement_alloc", "{}") if self.mastery_row else "{}"
        )

        for node_key in NATURE_ATTUNEMENT_NODE_ORDER:
            node = NATURE_ATTUNEMENT_TREE[node_key]
            invested = att_progress.get(node_key, 0)
            label = f"{node['label']} ({invested}/5)"
            btn = Button(
                label=label,
                style=ButtonStyle.primary if invested < 5 else ButtonStyle.success,
                row=0,
                disabled=not gate_met or invested >= 5,
            )
            btn.callback = self._make_invest_cb(node_key)
            self.add_item(btn)

        insight_data = get_total_insight_bonuses(self.mastery_row or {})
        insight_btn = Button(
            label=f"Insight: {insight_data['count']}",
            style=ButtonStyle.secondary,
            row=1,
            disabled=True,
        )
        self.add_item(insight_btn)

        back_btn = Button(label="Back", style=ButtonStyle.secondary, row=1)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    def _make_invest_cb(self, node_key: str):
        async def cb(interaction: Interaction):
            if self._processing:
                await interaction.response.defer()
                return
            self._processing = True
            await interaction.response.defer()

            pts_m = self._safe_get(self.mastery_row, "mining_points", 0)
            pts_f = self._safe_get(self.mastery_row, "fishing_points", 0)
            pts_w = self._safe_get(self.mastery_row, "woodcutting_points", 0)
            total_pts = pts_m + pts_f + pts_w

            if total_pts < 1:
                await interaction.followup.send(
                    "You have no unspent Artisan Points to invest.", ephemeral=True
                )
                self._processing = False
                return

            alloc_json = (
                self._safe_get(self.mastery_row, "attunement_alloc", "{}") or "{}"
            )
            new_alloc, spent, maxed = invest_in_attunement(
                alloc_json, total_pts, node_key, amount=1
            )

            if spent <= 0:
                await interaction.followup.send(
                    "Could not invest (node maxed or no points).", ephemeral=True
                )
                self._processing = False
                return

            candidates = [
                ("mining", self._safe_get(self.mastery_row, "mining_points", 0)),
                ("fishing", self._safe_get(self.mastery_row, "fishing_points", 0)),
                (
                    "woodcutting",
                    self._safe_get(self.mastery_row, "woodcutting_points", 0),
                ),
            ]
            candidates.sort(key=lambda x: x[1], reverse=True)
            chosen_skill = candidates[0][0] if candidates[0][1] > 0 else "mining"
            await self.bot.database.skills.deduct_mastery_points(
                self.user_id, self.server_id, chosen_skill, 1
            )
            await self.bot.database.skills.update_attunement_alloc(
                self.user_id, self.server_id, new_alloc
            )

            await self.refresh()
            await interaction.edit_original_response(embed=self.get_embed(), view=self)
            self._processing = False

        return cb

    async def back_callback(self, interaction: Interaction):
        if self._processing:
            await interaction.response.defer()
            return
        self._processing = True
        await interaction.response.defer()
        if self.parent_view:
            await self.parent_view.refresh()
            await interaction.edit_original_response(
                embed=(
                    self.parent_view.get_embed()
                    if hasattr(self.parent_view, "get_embed")
                    else None
                ),
                view=self.parent_view,
            )
        else:
            await interaction.delete_original_response()
        self.stop()

    def get_embed(self) -> discord.Embed:
        if not self.mastery_row:
            return discord.Embed(title="Nature's Attunement", description="Loading...")

        gate_met = has_nature_attunement_unlocked(self.mastery_row)
        att_progress = get_attunement_progress(
            self.mastery_row.get("attunement_alloc", "{}")
        )
        insight = get_total_insight_bonuses(self.mastery_row)

        if not gate_met:
            desc = "This cross-skill tree unlocks after you invest at least **20 points** in each of the three main trees (Mining, Woodcutting, and Fishing, including bonus investment)."
            return discord.Embed(
                title="🌿 Nature's Attunement — Locked",
                description=desc,
                color=0x555555,
            )

        lines = []
        for node_key in NATURE_ATTUNEMENT_NODE_ORDER:
            node = NATURE_ATTUNEMENT_TREE[node_key]
            inv = att_progress.get(node_key, 0)
            status = "MAX" if inv >= 5 else f"{inv}/5"
            lines.append(f"**{node['label']}** ({status})\n{node['desc']}")

        insight_lines = (
            f"**Mastery Insight:** {insight['count']}\n"
            f"• Global Yield: +{insight['global_yield'] * 100:.1f}%\n"
            f"• Remnant Chance: +{insight['remnant'] * 100:.1f}%\n"
            f"• Elemental Reprisal Chance: +{insight['reprisal'] * 100:.1f}%"
        )

        desc = (
            "Freely invest in any node (max 5 per node). Bonuses apply globally to all gathering.\n\n"
            + "\n\n".join(lines)
            + "\n\n"
            + insight_lines
        )

        embed = discord.Embed(
            title="🌿 Nature's Attunement", description=desc, color=0x2E8B57
        )
        embed.set_thumbnail(url=ARTISAN_MASTERY_ATTUNEMENT)
        embed.set_footer(
            text="Excess points after full mastery convert to Insight (5 pts = 1 Insight)."
        )
        return embed
