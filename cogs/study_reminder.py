# cogs/study_reminder.py
import datetime as dt
import random

import discord
from discord.ext import commands, tasks

from config import (
    DATA_FILE,
    MENTION_CHANNEL_ID,
    VOICE_CHANNEL_ID,
)
from state_store import StateStore
from time_utils import KST, now_kst, parse_iso

RANDOM_STUDY_MESSAGE = "{mention}님 공부하세요!"
INACTIVE_STUDY_MESSAGE = "{mention}\n{days}일 이상 공부 기록이 없습니다. 공부하세요!"
INACTIVE_STUDY_DAYS = 3


class StudyReminderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_study_reminder.start()

    def cog_unload(self):
        self.daily_study_reminder.cancel()

    @tasks.loop(time=dt.time(hour=12, minute=0, tzinfo=KST))
    async def daily_study_reminder(self):
        channel_id = MENTION_CHANNEL_ID
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

            allowed_mentions = discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
            )

            random_member = random.choice(candidates)
            await channel.send(
                RANDOM_STUDY_MESSAGE.format(mention=random_member.mention),
                allowed_mentions=allowed_mentions,
            )
            print(f"[STUDY] 랜덤 공부 알림 전송 완료: {now_kst().isoformat()} user={random_member.id}")

            store = StateStore(DATA_FILE)
            store.load()
            cutoff = now_kst() - dt.timedelta(days=INACTIVE_STUDY_DAYS)
            fallback_iso = store.state.get("study_tracking_started_at")
            fallback_at = parse_iso(fallback_iso) if fallback_iso else now_kst()
            if fallback_at.tzinfo is None:
                fallback_at = fallback_at.replace(tzinfo=KST)

            target_voice = guild.get_channel(VOICE_CHANNEL_ID)
            active_user_ids = {
                member.id
                for member in getattr(target_voice, "members", [])
                if not member.bot
            }

            inactive_members = []
            for member in guild.members:
                if member.bot or member.id in active_user_ids:
                    continue

                last_study_iso = store.state["last_study_at"].get(str(member.id))
                last_study_at = parse_iso(last_study_iso) if last_study_iso else fallback_at
                if last_study_at.tzinfo is None:
                    last_study_at = last_study_at.replace(tzinfo=KST)
                if last_study_at <= cutoff:
                    inactive_members.append(member)

            if not inactive_members:
                print("[STUDY] 며칠간 안 들어온 멤버가 없어 공부 알림 생략")
                return

            mention_list = " ".join(member.mention for member in inactive_members)
            content = INACTIVE_STUDY_MESSAGE.format(
                mention=mention_list,
                days=INACTIVE_STUDY_DAYS,
            )
            await channel.send(
                content,
                allowed_mentions=allowed_mentions,
            )
            user_ids = ",".join(str(member.id) for member in inactive_members)
            print(f"[STUDY] 미기록자 공부 알림 전송 완료: {now_kst().isoformat()} users={user_ids}")
        except Exception as e:
            print(f"[STUDY] 공부 알림 전송 실패: {e}")

    @daily_study_reminder.before_loop
    async def before_daily_study_reminder(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(StudyReminderCog(bot))
