import asyncio
import discord
from discord.ext import commands

from config import BOT_TOKEN, GUILD_ID
from helpers import ensure_dirs

EXTENSIONS = [
    "cogs.tickets",
    "cogs.logs",
    "cogs.admin_tools",
    "cogs.battle_monitor",
]


class FamBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self):
        ensure_dirs()

        for ext in EXTENSIONS:
            await self.load_extension(ext)
            print(f"[COG] loaded: {ext}")

        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            print(f"[SYNC] {len(synced)} guild commands")
        else:
            synced = await self.tree.sync()
            print(f"[SYNC] {len(synced)} global commands")

    async def on_ready(self):
        print("=" * 50)
        print(f"Logged in as: {self.user} ({self.user.id})")
        print(f"Guilds: {len(self.guilds)}")
        print("=" * 50)


async def main():
    bot = FamBot()
    async with bot:
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())