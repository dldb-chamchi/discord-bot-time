# cogs/voice_time.py
import datetime as dt
import asyncio
import aiohttp
from typing import List

import discord
from discord.ext import commands, tasks

from config import (
    VOICE_CHANNEL_ID, 
    REPORT_CHANNEL_ID_ENTER, 
    DATA_FILE, 
    REPORT_CHANNEL_ID_ALARM,
    REPORT_CHANNEL_ID_DAILY,
    NOTION_TOKEN 
)
from time_utils import now_kst, iso, KST
from state_store import StateStore

COOLDOWN_SECONDS = 10 * 60  # 10분

class VoiceTimeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = StateStore(DATA_FILE)
        self.store.load()

        self.channel_active = False
        self.last_alert_time: dt.datetime | None = None

        # 주간 리포트 태스크 시작
        self.daily_reporter.start()

    def cog_unload(self):
        self.daily_reporter.cancel()

    # 노션 일정을 실제 퇴장 시간으로 업데이트하는 내부 함수
    async def _update_notion_end_time(self, page_id: str, start_iso: str, actual_leave_iso: str):
        url = f"https://api.notion.com/v1/pages/{page_id}"
        headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        payload = {
            "properties": {
                "날짜": {
                    "date": {
                        "start": start_iso,
                        "end": actual_leave_iso
                    }
                }
            }
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.patch(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        print(f"[NOTION] 페이지 {page_id} 시간 업데이트 성공")
                    else:
                        text = await resp.text()
                        print(f"[NOTION] 업데이트 실패 ({resp.status}): {text}")
            except Exception as e:
                print(f"[NOTION] API 요청 중 오류 발생: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        target_id = VOICE_CHANNEL_ID
        uid = str(member.id)

        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None

        # 1. 입장 (Enter)
        if before_id != target_id and after_id == target_id:
            # 세션 시작 시간 기록
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

            # 채널에 아무도 없다가 첫 입장 시 알림
            if not self.channel_active and members_in_channel and cooldown_ok:
                self.channel_active = True
                self.last_alert_time = now

                # 동시 입장 보정을 위해 잠시 대기
                await asyncio.sleep(1)

                members_not_in_channel = [
                    m for m in guild.members
                    if not m.bot and m not in voice_channel.members
                ]

                report_ch = self.bot.get_channel(REPORT_CHANNEL_ID_ENTER) \
                    or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ENTER)
                
                header = f'음성 채널 **{voice_channel.name}**에 멤버가 있습니다!'

                if members_not_in_channel:
                    await self._send_mentions_in_chunks(report_ch, members_not_in_channel, header_text=header)
                else:
                    await report_ch.send(header)
            return

        # 2. 퇴장 (Leave)
        if before_id == target_id and after_id != target_id:
            leave_time = now_kst()
            
            # 칭찬용 세션 시간 계산
            start_iso = self.store.state["sessions"].get(uid)
            session_seconds = 0
            if start_iso:
                start_dt = dt.datetime.fromisoformat(start_iso)
                session_seconds = int((leave_time - start_dt).total_seconds())

            # 누적 시간 저장 및 세션 종료
            self.store.add_session_time(member.id)
            self.store.state["sessions"].pop(uid, None)
            self.store.save()

            # 채널이 비었는지 확인
            if before.channel and len([m for m in before.channel.members if not m.bot]) == 0:
                self.channel_active = False

            # --- [기능 1] 목표 초과 달성 칭찬 로직 ---
            if hasattr(self.bot, 'active_schedules') and member.id in self.bot.active_schedules:
                today = leave_time.date()
                if not hasattr(self.bot, 'last_praise_date') or self.bot.last_praise_date != today:
                    self.bot.praised_today = set()
                    self.bot.last_praise_date = today

                sched_info = self.bot.active_schedules[member.id]
                planned_start = sched_info["start"]
                planned_end = sched_info["end"]
                
                planned_seconds = int((planned_end - planned_start).total_seconds())
                total_seconds = self.store.state["totals"].get(uid, 0)

                if total_seconds > planned_seconds and member.id not in self.bot.praised_today:
                    praise_ch = self.bot.get_channel(REPORT_CHANNEL_ID_DAILY) or \
                                await self.bot.fetch_channel(REPORT_CHANNEL_ID_DAILY)
                    if praise_ch:
                        over_time_min = (total_seconds - planned_seconds) // 60
                        await praise_ch.send(
                            f"🎊 **{member.mention} 님, 정말 대단해요!**\n"
                            f"오늘 계획했던 시간보다 **{over_time_min}분**이나 더 공부하셨습니다! 🏆\n"
                            f"목표를 초과 달성하신 당신을 응원합니다! 👏👏👏"
                        )
                        self.bot.praised_today.add(member.id)

            # --- [기능 2] 조기 퇴장 감지 프로세스 (1단계: 경고 -> 2단계: 처분) ---
            if hasattr(self.bot, 'active_schedules') and member.id in self.bot.active_schedules:
                # ---------------------------------------------------------
                # [단계 1] 60초 경고 알림 로직 (추가된 기능)
                # ---------------------------------------------------------
                await asyncio.sleep(60) # 60초 대기

                # 1. 60초 후 복귀 여부 확인
                current_member = member.guild.get_member(member.id)
                is_back = False
                if current_member and current_member.voice and current_member.voice.channel:
                    if current_member.voice.channel.id == target_id:
                        is_back = True
                
                # 돌아왔다면 전체 로직 종료
                if is_back:
                    return

                # 아직 안 돌아왔다면 경고 메시지 전송
                sched_info = self.bot.active_schedules[member.id]
                scheduled_end = sched_info["end"]
                now = now_kst()

                if now < scheduled_end:
                    time_diff = scheduled_end - now
                    minutes_left = int(time_diff.total_seconds() / 60)
                    
                    if minutes_left > 1:
                        alarm_ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) \
                                   or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                        if alarm_ch:
                            msg = (
                                f"🚨 **{member.mention} 님, 어디 가시나요?**\n"
                                f"아직 일정이 **{minutes_left}분** 남았습니다! 얼른 돌아오세요!\n"
                                f"목표 시간: {scheduled_end.strftime('%H:%M')}"
                            )
                            await alarm_ch.send(msg)

                # ---------------------------------------------------------
                # [단계 2] 10분 미복귀 시 노션 수정 로직 (기존 기능)
                # ---------------------------------------------------------
                # 이미 60초를 기다렸으므로, 나머지 9분(540초)만 더 기다립니다.
                await asyncio.sleep(540) 

                # 2. 총 10분 후 복귀 여부 재확인
                current_member = member.guild.get_member(member.id)
                is_back_final = False
                if current_member and current_member.voice and current_member.voice.channel:
                    if current_member.voice.channel.id == target_id:
                        is_back_final = True
                
                # 돌아왔다면 종료
                if is_back_final:
                    return

                # 여전히 돌아오지 않았다면 -> 노션 일정 수정 및 최종 알림
                if leave_time < scheduled_end:
                    # 노션 업데이트 (종료 시간을 퇴장했던 시간으로 수정)
                    await self._update_notion_end_time(
                        sched_info["page_id"], 
                        sched_info["start"].isoformat(), 
                        leave_time.isoformat()
                    )

                    alarm_ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) \
                               or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                    if alarm_ch:
                        msg = (
                            f"⚠️ **{member.mention} 님, 10분 넘게 돌아오지 않으셨습니다.**\n"
                            f"노션의 일정을 실제 퇴장 시간({leave_time.strftime('%H:%M')})으로 수정하였습니다."
                        )
                        await alarm_ch.send(msg)
            return

    async def _send_mentions_in_chunks(
        self,
        report_ch: discord.abc.Messageable,
        members_to_ping: List[discord.Member],
        header_text: str = "",
        chunk_size: int = 40,
    ):
        """멘션이 많을 경우 2000자 제한을 피하기 위해 나누어 전송합니다."""
        for i in range(0, len(members_to_ping), chunk_size):
            chunk = members_to_ping[i : i + chunk_size]
            mention_list = " ".join(m.mention for m in chunk)
            text = f"{mention_list}\n{header_text}" if header_text else mention_list
            await report_ch.send(text)

    # 주간 리포트 (일요일 밤 11시 KST = 14:00 UTC)
    @tasks.loop(time=dt.time(hour=14, minute=0, tzinfo=dt.timezone.utc))
    async def daily_reporter(self):
        now = now_kst()
        if now.weekday() != 6: # 일요일이 아니면 종료
            return

        # 현재 진행 중인 세션이 있다면 임시 합산
        for uid in list(self.store.state["sessions"].keys()):
            self.store.add_session_time(int(uid), until=now)
            self.store.state["sessions"][uid] = iso(now)

        # 리포트 생성
        if not self.store.state["totals"]:
            content = "이번 주 대상 음성 채널 체류 기록이 없습니다."
        else:
            items = sorted(self.store.state["totals"].items(), key=lambda kv: kv[1], reverse=True)
            lines = ["이번 주 음성 채널 체류 시간 (일~토, 단위: 시간)"]
            for uid, sec in items:
                hours = sec / 3600.0
                lines.append(f"- <@{uid}>: {hours:.2f}h")
            content = "\n".join(lines)

        # 리포트 전송 및 데이터 초기화
        channel = self.bot.get_channel(REPORT_CHANNEL_ID_ENTER) \
            or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ENTER)
        try:
            await channel.send(content)
        finally:
            self.store.state["totals"] = {}
            self.store.save()

    # 누적 시간 수동 확인 명령어 (관리자 전용)
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