# cogs/study_reminder.py
import datetime as dt
import random

import discord
from discord.ext import commands, tasks

from config import (
    REPORT_CHANNEL_ID_CHASE,
    REPORT_CHANNEL_ID_DAILY,
    MENTION_CHANNEL_ID,
)
from time_utils import KST, now_kst

STUDY_REMINDER_MESSAGE = "{mention}님 공부하세요!"


class StudyReminderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_study_reminder.start()

    def cog_unload(self):
        self.daily_study_reminder.cancel()

    @tasks.loop(time=dt.time(hour=12, minute=0, tzinfo=KST))
    async def daily_study_reminder(self):
        channel_id = (
            MENTION_CHANNEL_ID
            or REPORT_CHANNEL_ID_CHASE
            or REPORT_CHANNEL_ID_DAILY
        )
        if not channel_id:
            print("[STUDY] MENTION_CHANNEL_ID 미설정으로 공부 알림 생략")
            return

        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            guild = getattr(channel, "guild", None)
            if not guild:
                print("[STUDY] 서버 채널이 아니라 공부 알림을 보낼 수 없습니다.")
                return

            candidates = [member for member in guild.members if not member.bot]
            if not candidates:
                print("[STUDY] 태그할 서버 멤버가 없어 공부 알림 생략")
                return

            member = random.choice(candidates)
            content = STUDY_REMINDER_MESSAGE.format(
                mention=member.mention,
                user_id=member.id,
                display_name=member.display_name,
            )
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )
            print(f"[STUDY] 공부 알림 전송 완료: {now_kst().isoformat()} user={member.id}")
        except Exception as e:
            print(f"[STUDY] 공부 알림 전송 실패: {e}")

    @daily_study_reminder.before_loop
    async def before_daily_study_reminder(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(StudyReminderCog(bot))
