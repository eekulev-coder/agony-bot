from __future__ import annotations

import io
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config import (
    ADMIN_ROLE_ID,
    SPECIAL_ROLE_ID,
    TICKET_CATEGORY_ID,
    TICKET_LOG_CHANNEL_ID,
    EMBED_COLOR,
    MAX_OPEN_TICKETS_PER_USER,
)
from helpers import (
    is_admin,
    extract_ticket_owner_id,
    sanitize_channel_name,
    read_json,
    write_json,
    fmt_dt,
    url_to_discord_file,
    split_chunks,
)


class CloseTicketView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Закрыть тикет",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="persistent_ticket_close"
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Только на сервере.", ephemeral=True)

        if not is_admin(interaction.user):
            return await interaction.response.send_message("❌ Только админ может закрыть тикет.", ephemeral=True)

        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Не тот канал.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        owner_id = extract_ticket_owner_id(channel.topic)
        owner = interaction.guild.get_member(owner_id) if owner_id else None

        transcript_text, attachment_refs, msg_count = await self.build_transcript(channel)

        transcript_bytes = transcript_text.encode("utf-8", errors="ignore")
        transcript_file = discord.File(
            io.BytesIO(transcript_bytes),
            filename=f"{channel.name}-transcript.txt"
        )

        log_channel = interaction.guild.get_channel(TICKET_LOG_CHANNEL_ID)

        if log_channel:
            embed = discord.Embed(
                title="🔒 Тикет закрыт",
                color=0xE74C3C,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(
                name="📋 Тикет",
                value=f"`{channel.name}`",
                inline=True
            )
            embed.add_field(
                name="📨 Сообщений",
                value=f"`{msg_count}`",
                inline=True
            )
            embed.add_field(
                name="📎 Вложений",
                value=f"`{len(attachment_refs)}`",
                inline=True
            )
            embed.add_field(
                name="👤 Создал",
                value=f"{owner.mention if owner else 'Не найден'}\n`{owner_id or 'unknown'}`",
                inline=True
            )
            embed.add_field(
                name="🛠️ Закрыл",
                value=f"{interaction.user.mention}\n`{interaction.user.id}`",
                inline=True
            )
            embed.add_field(
                name="\u200b",
                value="\u200b",
                inline=True
            )
            embed.set_footer(text="Транскрипт и вложения ниже")

            await log_channel.send(embed=embed)
            await log_channel.send(file=transcript_file)

            if attachment_refs:
                async with aiohttp.ClientSession() as session:
                    files = []
                    fallback_urls = []

                    for idx, att in enumerate(attachment_refs, start=1):
                        file_obj = await url_to_discord_file(
                            session=session,
                            url=att["url"],
                            filename=att["filename"],
                            prefix=f"{idx}_"
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

        await channel.delete(reason=f"Ticket closed by {interaction.user} ({interaction.user.id})")

    async def build_transcript(self, channel: discord.TextChannel):
        lines = []
        attachment_refs = []
        seen_urls = set()
        msg_count = 0

        lines.append(f"═══════════════════════════════════════════")
        lines.append(f"  ТРАНСКРИПТ ТИКЕТА: {channel.name}")
        lines.append(f"  Дата закрытия: {fmt_dt(discord.utils.utcnow())}")
        lines.append(f"═══════════════════════════════════════════")
        lines.append("")

        async for msg in channel.history(limit=None, oldest_first=True):
            msg_count += 1
            lines.append(f"[{fmt_dt(msg.created_at)}] {msg.author} ({msg.author.id})")
            lines.append(msg.content if msg.content else "")

            if msg.attachments:
                for att in msg.attachments:
                    lines.append(f"  📎 {att.filename} | {att.url}")
                    if att.url not in seen_urls:
                        seen_urls.add(att.url)
                        attachment_refs.append({
                            "url": att.url,
                            "filename": att.filename
                        })

            if msg.embeds:
                for e in msg.embeds:
                    if e.title:
                        lines.append(f"  [EMBED] {e.title}")

            lines.append("")

        if msg_count == 0:
            lines.append("Пустой тикет.")

        return "\n".join(lines), attachment_refs, msg_count


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Регнуть тикет",
        style=discord.ButtonStyle.secondary,
        emoji="📝",
        custom_id="persistent_ticket_create"
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Только на сервере.", ephemeral=True)

        category = interaction.guild.get_channel(TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ Категория тикетов не найдена.", ephemeral=True)

        open_count = 0
        for ch in category.text_channels:
            owner_id = extract_ticket_owner_id(ch.topic)
            if owner_id == interaction.user.id:
                open_count += 1

        if open_count >= MAX_OPEN_TICKETS_PER_USER:
            return await interaction.response.send_message(
                f"❌ У тебя уже открыто **{MAX_OPEN_TICKETS_PER_USER}** тикета. Закрой один из них, чтобы создать новый.",
                ephemeral=True
            )

        state = read_json("data/ticket_state.json", {"counter": 0})
        state["counter"] += 1
        counter = state["counter"]
        write_json("data/ticket_state.json", state)

        username = sanitize_channel_name(interaction.user.name)
        channel_name = f"ticket-{username}-{counter}"

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            ),
            interaction.guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True
            )
        }

        admin_role = interaction.guild.get_role(ADMIN_ROLE_ID)
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True
            )

        special_role = interaction.guild.get_role(SPECIAL_ROLE_ID)
        if special_role:
            overwrites[special_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"ticket_owner:{interaction.user.id}|ticket_number:{counter}"
        )

        # Красивая анкета в тикете
        welcome_embed = discord.Embed(
            title=f"📝 Заявка #{counter}",
            description=(
                f"Привет, {interaction.user.mention}!\n"
                f"Заполни, пожалуйста, анкету ниже 👇\n\n"
                f"Тикет будет рассмотрен в ближайшее время."
            ),
            color=EMBED_COLOR
        )

        form_embed = discord.Embed(
            title="📋 Анкета",
            color=0x2ECC71
        )
        form_embed.add_field(
            name="1️⃣ История фамок",
            value="_В каких фамах был раньше?_",
            inline=False
        )
        form_embed.add_field(
            name="2️⃣ Лвл (скриншоты)",
            value="_Прикрепи скриншоты уровня_",
            inline=False
        )
        form_embed.add_field(
            name="3️⃣ 2 видео за неделю",
            value="_Прикрепи 2 видео с игрового процесса_",
            inline=False
        )
        form_embed.add_field(
            name="4️⃣ Был ли ЧС?",
            value="_Опиши ситуации с ЧС если были_",
            inline=False
        )
        form_embed.set_footer(text="Отвечай прямо в этом канале • Прикрепляй файлы")

        close_view = CloseTicketView(self.bot)
        await channel.send(
            content=f"{interaction.user.mention}",
            embeds=[welcome_embed, form_embed],
            view=close_view
        )

        # Красивое лог-сообщение
        log_channel = interaction.guild.get_channel(TICKET_LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title="🆕 Тикет создан",
                color=0x2ECC71,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="📋 Тикет", value=f"`{channel.name}`", inline=True)
            embed.add_field(name="🔢 Номер", value=f"`#{counter}`", inline=True)
            embed.add_field(name="📍 Канал", value=channel.mention, inline=True)
            embed.add_field(
                name="👤 Пользователь",
                value=f"{interaction.user.mention}\n`{interaction.user}`\n`{interaction.user.id}`",
                inline=False
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            await log_channel.send(embed=embed)

        await interaction.response.send_message(
            f"✅ Твой тикет создан: {channel.mention}",
            ephemeral=True
        )


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(TicketPanelView(bot))
        self.bot.add_view(CloseTicketView(bot))

    @app_commands.command(name="ticketpanel", description="Отправить кнопку создания тикета")
    async def ticketpanel(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Только на сервере.", ephemeral=True)

        if not is_admin(interaction.user):
            return await interaction.response.send_message("Нет прав.", ephemeral=True)

        view = TicketPanelView(self.bot)
        await interaction.channel.send("\u200b", view=view)
        await interaction.response.send_message("✅ Панель отправлена.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))