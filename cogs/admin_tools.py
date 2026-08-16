from __future__ import annotations

import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from config import EMBED_COLOR, CLEAR_LIMIT
from helpers import is_admin


class AdminTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="announce", description="Объявление с @everyone")
    @app_commands.describe(text="Текст объявления")
    async def announce(self, interaction: discord.Interaction, text: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Только на сервере.", ephemeral=True)

        if not is_admin(interaction.user):
            return await interaction.response.send_message("Нет прав.", ephemeral=True)

        embed = discord.Embed(
            description=text,
            color=EMBED_COLOR
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

        await interaction.channel.send("@everyone", embed=embed)
        await interaction.response.send_message("Отправлено.", ephemeral=True)

    @app_commands.command(name="poll", description="Простой опрос")
    @app_commands.describe(question="Вопрос")
    async def poll(self, interaction: discord.Interaction, question: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Только на сервере.", ephemeral=True)

        if not is_admin(interaction.user):
            return await interaction.response.send_message("Нет прав.", ephemeral=True)

        embed = discord.Embed(
            title="Опрос",
            description=question,
            color=EMBED_COLOR
        )
        embed.set_footer(text=f"Создал: {interaction.user.display_name}")

        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        await interaction.response.send_message("Опрос создан.", ephemeral=True)

    @app_commands.command(name="clear", description="Удалить сообщения")
    @app_commands.describe(
        count="Сколько удалить (макс. 100)",
        member="Если указать - удалятся только его сообщения"
    )
    async def clear(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, CLEAR_LIMIT],
        member: discord.Member | None = None
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Только на сервере.", ephemeral=True)

        if not is_admin(interaction.user):
            return await interaction.response.send_message("Нет прав.", ephemeral=True)

        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Команда работает только в текстовом канале.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        deleted_count = 0

        if member is None:
            deleted = await interaction.channel.purge(limit=count)
            deleted_count = len(deleted)
        else:
            to_delete = []
            async for msg in interaction.channel.history(limit=1000):
                if msg.author.id == member.id:
                    to_delete.append(msg)
                    if len(to_delete) >= count:
                        break

            for msg in to_delete:
                try:
                    await msg.delete()
                    deleted_count += 1
                    await asyncio.sleep(0.15)
                except Exception:
                    pass

        await interaction.followup.send(f"Удалено: **{deleted_count}**", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminTools(bot))