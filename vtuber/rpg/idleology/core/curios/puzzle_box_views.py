import asyncio

import discord
from discord import ButtonStyle, Interaction, ui

from vtuber.rpg.idleology.core.base_view import BaseView
from vtuber.rpg.idleology.core.curios.puzzle_box_logic import (
    PUZZLE_BOX_IMAGE,
    REWARD_EMOJIS,
    claim_rewards,
    format_slot_display,
    roll_all_slots,
    roll_slot,
)
from vtuber.rpg.idleology.core.emojis import CURIO, PUZZLE_BOX


class PuzzleBoxView(BaseView):
    def __init__(self, bot, user_id: str, server_id: str):
        # timeout=None: discord.py's own View timeout resets on every
        # dispatched interaction (see discord.ui.View._scheduled_task), so a
        # reroll would silently extend that window. The 60s window here must
        # be a hard deadline from when the box was opened, reroll count
        # aside, so it's driven entirely by our own _countdown_loop task
        # instead — see that method for the actual auto-claim.
        super().__init__(bot=bot, user_id=user_id, server_id=server_id, timeout=None)
        self.bot = bot
        self.user_id = user_id
        self.server_id = server_id
        self.slots: list[tuple[str, int]] = roll_all_slots()
        self.claimed = False
        self.message = None
        self._reward_records: list[dict] = []
        self._remaining_seconds: int = 60
        self._build_buttons()
        self._countdown_task = asyncio.create_task(self._countdown_loop())

    def _build_buttons(self):
        self.clear_items()

        for i in range(3):
            btn = ui.Button(
                label=f"Reroll Slot {i + 1}",
                style=ButtonStyle.blurple,
                emoji="🎲",
                disabled=self.claimed,
                row=0,
                custom_id=f"reroll_{i}",
            )
            slot_index = i
            btn.callback = lambda interaction, idx=slot_index: self._reroll_slot(
                interaction, idx
            )
            self.add_item(btn)

        btn_claim = ui.Button(
            label="Claim Rewards",
            style=ButtonStyle.success,
            emoji="✅",
            disabled=self.claimed,
            row=1,
        )
        btn_claim.callback = self._claim
        self.add_item(btn_claim)

    async def _countdown_loop(self):
        """Fixed 60s window from view creation — never restarted by rerolls,
        so the player gets exactly 60s to reroll as much as they like before
        rewards are auto-claimed."""
        for i in range(6):
            await asyncio.sleep(10)
            self._remaining_seconds = max(0, 60 - (i + 1) * 10)
            if self.claimed or not self.message:
                return
            if self._remaining_seconds > 0:
                # A single failed edit (rate limit, transient API hiccup) must
                # not permanently freeze the displayed countdown for the rest
                # of the window — _safe_message_edit retries once and logs
                # instead of aborting the loop outright.
                await self._safe_message_edit(embed=self.build_embed())
            else:
                await self._expire_and_claim()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"{PUZZLE_BOX} Curio Puzzle Box",
            description=(
                "Three reward slots are revealed. Reroll any slot to change its type and quantity. "
                "What you see is what you get."
            ),
            color=0x9B59B6,
        )
        embed.set_thumbnail(url=PUZZLE_BOX_IMAGE)

        for i, (rtype, qty) in enumerate(self.slots, 1):
            emoji = REWARD_EMOJIS[rtype]
            display = format_slot_display(rtype, qty)
            embed.add_field(
                name=f"Slot {i}",
                value=f"{emoji} **{rtype}**\n{display}",
                inline=True,
            )

        embed.set_footer(
            text=f"⏳ {self._remaining_seconds}s remaining · Auto-claims on expiry"
        )
        return embed

    def build_claimed_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"{PUZZLE_BOX} Puzzle Box Opened!",
            description="Here's what you walked away with:",
            color=0x2ECC71,
        )
        for record in self._reward_records:
            embed.add_field(
                name=f"{record['emoji']} {record['title']}",
                value="\n".join(record["lines"]),
                inline=False,
            )
        embed.set_image(url=PUZZLE_BOX_IMAGE)
        return embed

    async def _reroll_slot(self, interaction: Interaction, slot_index: int):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message(
                "This isn't your session.", ephemeral=True
            )

        used = {rtype for i, (rtype, _) in enumerate(self.slots) if i != slot_index}
        self.slots[slot_index] = roll_slot(exclude=used)
        # Note: the 60s window is not extended here — see _countdown_loop.
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _claim(self, interaction: Interaction):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message(
                "This isn't your session.", ephemeral=True
            )
        if self.claimed:
            return
        self.claimed = True  # Set before defer to prevent concurrent double-claims
        # Cross-task cancel: this callback runs on a different task than
        # _countdown_loop, so this is safe (contrast _expire_and_claim below,
        # which runs *inside* that same loop task and must not self-cancel it).
        self._countdown_task.cancel()

        await interaction.response.defer()
        await self._do_claim()
        claimed_view = _ClaimedPuzzleBoxView(self.bot, self.user_id, self.server_id)
        await interaction.edit_original_response(
            embed=self.build_claimed_embed(), view=claimed_view
        )
        claimed_view.message = await interaction.original_response()
        self.stop()

    async def _do_claim(self):
        self.claimed = True
        self._reward_records = await claim_rewards(
            self.bot, self.user_id, self.server_id, self.slots
        )
        self.bot.state_manager.clear_active(self.user_id)

    async def _expire_and_claim(self):
        """Fires from _countdown_loop once the fixed 60s window elapses
        without an explicit Claim click — auto-claims whatever is showing."""
        if self.claimed or not self.message:
            return
        try:
            await self._do_claim()
            claimed_view = _ClaimedPuzzleBoxView(self.bot, self.user_id, self.server_id)
            await self.message.edit(embed=self.build_claimed_embed(), view=claimed_view)
            claimed_view.message = self.message
        except (discord.NotFound, discord.HTTPException, AttributeError):
            pass
        except Exception:
            # An unexpected failure here (e.g. a DB error inside claim_rewards)
            # must not strand the player on a frozen message with dead
            # buttons — log it and fall through to self.stop() below.
            try:
                self.bot.logger.error(
                    f"PuzzleBoxView auto-claim on expiry failed for user {self.user_id}",
                    exc_info=True,
                )
            except Exception:
                pass
        self.stop()


class _ClaimedPuzzleBoxView(BaseView):
    """Shown after a puzzle box has been claimed, manually or via auto-claim timeout."""

    def __init__(self, bot, user_id: str, server_id: str):
        super().__init__(bot=bot, user_id=user_id, server_id=server_id)
        btn_back = ui.Button(
            label="Back to Curios", style=ButtonStyle.secondary, emoji=CURIO, row=0
        )
        btn_back.callback = self._back_to_curios
        self.add_item(btn_back)

    async def _back_to_curios(self, interaction: Interaction):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message(
                "This isn't your session.", ephemeral=True
            )

        from vtuber.rpg.idleology.core.curios.views import CurioView

        user_id = self.user_id
        server_id = self.server_id
        cur = await self.bot.database.users.get_all_currencies(user_id)
        curio_count = cur["curios"]
        puzzle_box_count = cur["curio_puzzle_boxes"]

        if curio_count <= 0 and puzzle_box_count <= 0:
            self.stop()
            await interaction.response.edit_message(
                embed=discord.Embed(
                    description="You have no more Curios or Puzzle Boxes.",
                    color=discord.Color.dark_grey(),
                ),
                view=None,
            )
            return

        self.bot.state_manager.set_active(user_id, "curios")
        self.stop()
        view = CurioView(self.bot, user_id, server_id, curio_count, puzzle_box_count)
        await interaction.response.edit_message(embed=view.build_hub_embed(), view=view)
        view.message = await interaction.original_response()
