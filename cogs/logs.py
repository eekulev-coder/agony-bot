from __future__ import annotations

import asyncio
from collections import OrderedDict
import aiohttp
import discord
from discord.ext import commands

from config import SERVER_LOG_CHANNEL_ID, EMBED_COLOR
from helpers import fmt_dt, escape_text, url_to_discord_file, split_chunks


class Logs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.message_cache: OrderedDict[int, dict] = OrderedDict()
        self.cache_limit = 10000

    def cache_message(self, message: discord.Message):
        # Игнорируем ботов и вебхуки
        if not message.guild:
            return
        if message.author.bot:
            return
        if message.webhook_id:
            return

        self.message_cache[message.id] = {
            "message_id": message.id,
            "guild_id": message.guild.id,
            "channel_id": message.channel.id,
            "author_id": message.author.id,
            "author_tag": str(message.author),
            "author_bot": message.author.bot,
            "content": message.content,
            "created_at": message.created_at,
            "attachments": [
                {"filename": a.filename, "url": a.url}
                for a in message.attachments
            ]
        }

        while len(self.message_cache) > self.cache_limit:
            self.message_cache.popitem(last=False)

    async def get_log_channel(self, guild: discord.Guild):
        channel = guild.get_channel(SERVER_LOG_CHANNEL_ID)
        return channel if isinstance(channel, discord.TextChannel) else None

    async def find_audit_actor(self, guild: discord.Guild, target_id: int, action: discord.AuditLogAction):
        await asyncio.sleep(1.0)
        try:
            async for entry in guild.audit_logs(limit=6, action=action):
                if not entry.target:
                    continue
                if getattr(entry.target, "id", None) != target_id:
                    continue

                delta = abs((discord.utils.utcnow() - entry.created_at).total_seconds())
                if delta <= 15:
                    return entry.user
        except Exception:
            return None
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        self.cache_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # Игнор ботов и вебхуков
        if not before.guild:
            return
        if before.author.bot or after.author.bot:
            return
        if before.webhook_id or after.webhook_id:
            return

        before_attachments = [a.url for a in before.attachments]
        after_attachments = [a.url for a in after.attachments]

        if before.content == after.content and before_attachments == after_attachments:
            self.cache_message(after)
            return

        log_channel = await self.get_log_channel(before.guild)
        if not log_channel:
            self.cache_message(after)
            return

        embed = discord.Embed(title="✏️ Изменено сообщение", color=EMBED_COLOR)
        embed.add_field(name="Пользователь", value=f"{before.author.mention}\n`{before.author.id}`", inline=False)
        embed.add_field(name="Канал", value=before.channel.mention, inline=False)
        embed.add_field(name="Было", value=escape_text(before.content)[:1024], inline=False)
        embed.add_field(name="Стало", value=escape_text(after.content)[:1024], inline=False)
        embed.add_field(name="Время", value=fmt_dt(discord.utils.utcnow()), inline=False)

        await log_channel.send(embed=embed)
        self.cache_message(after)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if not payload.guild_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        log_channel = await self.get_log_channel(guild)
        if not log_channel:
            return

        record = self.message_cache.pop(payload.message_id, None)

        # Если сообщения нет в кэше — значит это был бот/вебхук (мы их не кэшируем)
        # Значит ЛОГИРОВАТЬ НЕ НАДО
        if not record:
            return

        # Дополнительная проверка на бота (страховка)
        if record.get("author_bot"):
            return

        embed = discord.Embed(title="🗑️ Удалено сообщение", color=EMBED_COLOR)
        embed.add_field(
            name="Пользователь",
            value=f"{escape_text(record['author_tag'])}\n`{record['author_id']}`",
            inline=False
        )

        channel = guild.get_channel(record["channel_id"])
        embed.add_field(
            name="Канал",
            value=channel.mention if channel else f"`{record['channel_id']}`",
            inline=False
        )
        embed.add_field(name="Текст", value=escape_text(record["content"])[:1024], inline=False)
        embed.add_field(name="Время", value=fmt_dt(discord.utils.utcnow()), inline=False)

        await log_channel.send(embed=embed)

        attachments = record.get("attachments", [])
        if attachments:
            await log_channel.send(f"📎 Вложений: {len(attachments)} шт.")

            async with aiohttp.ClientSession() as session:
                files = []
                fallback_urls = []

                for idx, att in enumerate(attachments, start=1):
                    file_obj = await url_to_discord_file(
                        session=session,
                        url=att["url"],
                        filename=att["filename"],
                        prefix=f"deleted_{idx}_"
                    )
                    if file_obj:
                        files.append(file_obj)
                    else:
                        fallback_urls.append(att["url"])

                for chunk in split_chunks(files, 10):
                    await log_channel.send(files=chunk)

                if fallback_urls:
                    text = "Не удалось перезалить:\n" + "\n".join(fallback_urls[:30])
                    await log_channel.send(text[:1900])

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return

        log_channel = await self.get_log_channel(after.guild)
        if not log_channel:
            return

        if before.display_name != after.display_name:
            actor = await self.find_audit_actor(after.guild, after.id, discord.AuditLogAction.member_update)

            embed = discord.Embed(title="📝 Смена ника", color=EMBED_COLOR)
            embed.add_field(name="Пользователь", value=f"{after.mention}\n`{after.id}`", inline=False)
            embed.add_field(name="Было", value=escape_text(before.display_name), inline=False)
            embed.add_field(name="Стало", value=escape_text(after.display_name), inline=False)
            embed.add_field(name="Изменил", value=actor.mention if actor else "Не удалось определить", inline=False)
            embed.add_field(name="Время", value=fmt_dt(discord.utils.utcnow()), inline=False)
            await log_channel.send(embed=embed)

        before_roles = {r.id: r for r in before.roles if r != before.guild.default_role}
        after_roles = {r.id: r for r in after.roles if r != after.guild.default_role}

        added = [role for rid, role in after_roles.items() if rid not in before_roles]
        removed = [role for rid, role in before_roles.items() if rid not in after_roles]

        if not added and not removed:
            return

        actor = await self.find_audit_actor(after.guild, after.id, discord.AuditLogAction.member_role_update)

        if added:
            embed = discord.Embed(title="➕ Выданы роли", color=0x2ECC71)
            embed.add_field(name="Пользователь", value=f"{after.mention}\n`{after.id}`", inline=False)
            embed.add_field(name="Роли", value="\n".join(role.mention for role in added)[:1024], inline=False)
            embed.add_field(name="Кем", value=actor.mention if actor else "Не удалось определить", inline=False)
            embed.add_field(name="Время", value=fmt_dt(discord.utils.utcnow()), inline=False)
            await log_channel.send(embed=embed)

        if removed:
            embed = discord.Embed(title="➖ Сняты роли", color=0xE74C3C)
            embed.add_field(name="Пользователь", value=f"{after.mention}\n`{after.id}`", inline=False)
            embed.add_field(name="Роли", value="\n".join(role.name for role in removed)[:1024], inline=False)
            embed.add_field(name="Кем", value=actor.mention if actor else "Не удалось определить", inline=False)
            embed.add_field(name="Время", value=fmt_dt(discord.utils.utcnow()), inline=False)
            await log_channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Logs(bot))