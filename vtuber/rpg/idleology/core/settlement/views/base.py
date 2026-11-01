# core/settlement/views/base.py
"""Base class for all settlement-related Discord UI views."""

import discord
from discord import Interaction, ui

from vtuber.rpg.idleology.core.base_view import BaseView


class SettlementBaseView(BaseView):
    """Common base for all settlement views.

    Provides a shared ``on_timeout`` that deletes the original message and
    clears the player's active state.  All settlement views share a single
    Discord message; child views (PlotDetailView, TownHallView, etc.) hold
    a ``parent`` reference back to the top-level dashboard (or an intermediate
    view), and ``_root_message`` walks that chain to find the message object.
    """

    # Settlement actions (Collect, Next Turn, Black Market deals, ...) can run
    # several sequential DB writes — long enough that a click landing mid-flight
    # deserves an explicit "still working" notice rather than a silent no-op,
    # which otherwise reads as the view having gone stale/unresponsive.
    _BUSY_MESSAGE = "⏳ Still processing your last action — try again in a moment."

    def __init__(self, bot, user_id: str, timeout: int = 600):
        super().__init__(bot, user_id, timeout=timeout)

    @property
    def _root_message(self):
        """Return the Discord Message to delete, climbing parent/origin chains."""
        if self.message:
            return self.message
        for attr in ("parent", "origin"):
            node = getattr(self, attr, None)
            if isinstance(node, SettlementBaseView):
                return node._root_message
        return None

    async def on_timeout(self) -> None:
        self.bot.state_manager.clear_active(self.user_id)
        msg = self._root_message
        if msg is not None:
            try:
                await msg.delete()
            except Exception:
                pass
        self.stop()

    async def on_error(
        self, interaction: Interaction, error: Exception, item: ui.Item
    ) -> None:
        """Central safety net for every settlement view.

        Without this, an unhandled exception inside a button/select callback
        (e.g. a DB call failing mid-collection) propagates past BaseView's
        re-entry guard — which correctly resets and lets later clicks
        dispatch again — but the *triggering* interaction is left with no
        followup: the "thinking" state clears with no visible change and no
        error message, which reads as the view having silently died. Always
        surface something to the player and log the failure server-side
        instead of dropping it.
        """
        try:
            self.bot.logger.error(
                f"{type(self).__name__} error in {type(item).__name__} "
                f"for user {self.user_id}",
                exc_info=error,
            )
        except Exception:
            pass

        message = "⚠️ Something went wrong processing that action. Please try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass
