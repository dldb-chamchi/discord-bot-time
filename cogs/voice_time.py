# cogs/voice_time.py
import datetime as dt
import asyncio
import aiohttp

import discord
from discord.ext import commands, tasks

from config import (
    VOICE_CHANNEL_ID,
    REPORT_CHANNEL_ID_ENTER,
    REPORT_CHANNEL_ID_ALARM,
    DATA_FILE,
    NOTION_TOKEN,
    NOTION_DATABASE_SCHEDULE_ID,
)
from time_utils import now_kst, iso, KST, parse_iso
from state_store import StateStore

COOLDOWN_SECONDS = 10 * 60  # 10분
MINIMUM_NOTION_RECORD_SECONDS = 30 * 60  # 30분
DISCORD_TO_NOTION_NAME = {
    "이유": "임아리",
    "SAK": "김성아",
    "민둥": "장민지",
}


class VoiceTimeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = StateStore(DATA_FILE)
        self.store.load()
        self.channel_active = False
        self.last_alert_time: dt.datetime | None = None
        self.daily_reporter.start()

    def cog_unload(self):
        self.daily_reporter.cancel()

    def _resolve_notion_name(self, member: discord.Member) -> str:
        for candidate in (member.display_name, member.name):
            if candidate in DISCORD_TO_NOTION_NAME:
                return DISCORD_TO_NOTION_NAME[candidate]
        return member.display_name

    async def _send_schedule_alert(self, notion_name: str, start_at: dt.datetime, end_at: dt.datetime):
        if not REPORT_CHANNEL_ID_ALARM:
            return

        channel = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
        line = (
            f"{notion_name} — "
            f"{start_at.astimezone(KST).strftime('%Y-%m-%d %H:%M')} ~ "
            f"{end_at.astimezone(KST).strftime('%Y-%m-%d %H:%M')}"
        )
        await channel.send(f"새 일정이 등록되었습니다 📅\n{line}")

    async def _create_notion_voice_record(self, member: discord.Member, start_at: dt.datetime, end_at: dt.datetime):
        if not NOTION_TOKEN or not NOTION_DATABASE_SCHEDULE_ID:
            return

        notion_name = self._resolve_notion_name(member)
        session_title = f"{notion_name} {start_at.strftime('%Y-%m-%d %H:%M')}"
        url = "https://api.notion.com/v1/pages"
        headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        payload = {
            "parent": {"database_id": str(NOTION_DATABASE_SCHEDULE_ID).strip()},
            "properties": {
                "이름": {
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": session_title},
                        }
                    ]
                },
                "날짜": {
                    "date": {
                        "start": start_at.astimezone(KST).isoformat(),
                        "end": end_at.astimezone(KST).isoformat(),
                    }
                },
                "태그": {
                    "multi_select": [{"name": notion_name}]
                },
            },
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status in (200, 201):
                        print(f"[NOTION] 음성 기록 생성 성공: {member.display_name}")
                        await self._send_schedule_alert(notion_name, start_at, end_at)
                    else:
                        text = await resp.text()
                        print(f"[NOTION] 음성 기록 생성 실패 ({resp.status}): {text}")
            except Exception as e:
                print(f"[NOTION] API 요청 중 오류 발생: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        target_id = VOICE_CHANNEL_ID
        uid = str(member.id)
        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None

        if before_id != target_id and after_id == target_id:
            print(f"[DEBUG] 입장 감지: {member.display_name} (ID: {uid})")
            self.store.state["sessions"][uid] = iso(now_kst())
            self.store.save()

            voice_channel = after.channel
            guild = member.guild
            if not voice_channel or not guild:
                return

            members_in_channel = [m for m in voice_channel.members if not m.bot]
            now = now_kst()
            cooldown_ok = (
                self.last_alert_time is None
                or (now - self.last_alert_time).total_seconds() > COOLDOWN_SECONDS
            )

            if not self.channel_active and members_in_channel and cooldown_ok:
                self.channel_active = True
                self.last_alert_time = now
                await asyncio.sleep(1)
                members_not_in_channel = [
                    m for m in guild.members if not m.bot and m not in voice_channel.members
                ]
                report_ch = self.bot.get_channel(REPORT_CHANNEL_ID_ENTER) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ENTER)
                header = f"음성 채널 **{voice_channel.name}**에 멤버가 있습니다!"
                if members_not_in_channel:
                    await self._send_mentions_in_chunks(report_ch, members_not_in_channel, header_text=header)
                else:
                    await report_ch.send(header)
            return

        if before_id == target_id and after_id != target_id:
            leave_time = now_kst()
            print(f"[DEBUG] 퇴장 감지: {member.display_name}")

            start_iso = self.store.state["sessions"].get(uid)
            session_seconds = self.store.add_session_time(member.id)
            self.store.state["sessions"].pop(uid, None)
            self.store.save()

            if before.channel and len([m for m in before.channel.members if not m.bot]) == 0:
                self.channel_active = False

            if start_iso and session_seconds >= MINIMUM_NOTION_RECORD_SECONDS:
                start_time = parse_iso(start_iso)
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=KST)
                await self._create_notion_voice_record(member, start_time, leave_time)
            else:
                print(f"[DEBUG] 30분 미만 세션이라 노션 기록 생략: {member.display_name} ({session_seconds}s)")
            return

    async def _send_mentions_in_chunks(self, report_ch, members_to_ping, header_text="", chunk_size=40):
        for i in range(0, len(members_to_ping), chunk_size):
            chunk = members_to_ping[i : i + chunk_size]
            mention_list = " ".join(m.mention for m in chunk)
            text = f"{mention_list}\n{header_text}" if header_text else mention_list
            await report_ch.send(text)

    @tasks.loop(time=dt.time(hour=14, minute=0, tzinfo=dt.timezone.utc))
    async def daily_reporter(self):
        now = now_kst()
        if now.weekday() != 6:
            return

        for uid in list(self.store.state["sessions"].keys()):
            self.store.add_session_time(int(uid), until=now)
            self.store.state["sessions"][uid] = iso(now)

        if not self.store.state["totals"]:
            content = "이번 주 대상 음성 채널 체류 기록이 없습니다."
        else:
            items = sorted(self.store.state["totals"].items(), key=lambda kv: kv[1], reverse=True)
            lines = ["이번 주 음성 채널 체류 시간 (일~토, 단위: 시간)"]
            for uid, sec in items:
                hours = sec / 3600.0
                lines.append(f"- <@{uid}>: {hours:.2f}h")
            content = "\n".join(lines)

        channel = self.bot.get_channel(REPORT_CHANNEL_ID_ENTER) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ENTER)
        try:
            await channel.send(content)
        finally:
            self.store.state["totals"] = {}
            self.store.save()

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def voicetime(self, ctx: commands.Context):
        if not self.store.state["totals"]:
            await ctx.send("현재 누적 데이터가 없습니다.")
            return

        items = sorted(self.store.state["totals"].items(), key=lambda kv: kv[1], reverse=True)
        lines = ["현재 누적 음성 채널 체류 시간:"]
        for uid, sec in items:
            hours = sec / 3600.0
            lines.append(f"<@{uid}>: {hours:.2f}h")
        await ctx.send("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceTimeCog(bot))
