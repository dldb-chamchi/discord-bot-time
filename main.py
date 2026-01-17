# main.py
import asyncio

from config import DISCORD_TOKEN
from bot import bot  # 위에서 만든 bot 인스턴스를 가져옵니다.

async def main():
    async with bot:
        # cogs 폴더에 있는 확장들을 여기서 로드합니다.
        await bot.load_extension("cogs.voice_time")
        await bot.load_extension("cogs.mention_shortcut")
        await bot.load_extension("cogs.menu_commands")
        await bot.load_extension("cogs.notion_watcher")
        await bot.load_extension("cogs.toeic_alarm")

        # 실제 디스코드 봇 실행
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
