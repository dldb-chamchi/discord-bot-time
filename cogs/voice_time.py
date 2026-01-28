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
        self.daily_reporter.start()

    def cog_unload(self):
        self.daily_reporter.cancel()

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
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        target_id = VOICE_CHANNEL_ID
        uid = str(member.id)
        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None

        # 1. 입장 (Enter)
        if before_id != target_id and after_id == target_id:
            print(f"[DEBUG] 입장 감지: {member.display_name} (ID: {uid})")
            self.store.state["sessions"][uid] = iso(now_kst())
            self.store.save()

            voice_channel = after.channel
            guild = member.guild
            if not voice_channel or not guild: return

            members_in_channel = [m for m in voice_channel.members if not m.bot]
            now = now_kst()
            cooldown_ok = (self.last_alert_time is None or (now - self.last_alert_time).total_seconds() > COOLDOWN_SECONDS)

            if not self.channel_active and members_in_channel and cooldown_ok:
                self.channel_active = True
                self.last_alert_time = now
                await asyncio.sleep(1)
                members_not_in_channel = [m for m in guild.members if not m.bot and m not in voice_channel.members]
                report_ch = self.bot.get_channel(REPORT_CHANNEL_ID_ENTER) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ENTER)
                header = f'음성 채널 **{voice_channel.name}**에 멤버가 있습니다!'
                if members_not_in_channel:
                    await self._send_mentions_in_chunks(report_ch, members_not_in_channel, header_text=header)
                else:
                    await report_ch.send(header)
            return

        # 2. 퇴장 (Leave)
        if before_id == target_id and after_id != target_id:
            leave_time = now_kst()
            print(f"[DEBUG] 퇴장 감지: {member.display_name}")

            # 세션 처리
            start_iso = self.store.state["sessions"].get(uid)
            session_seconds = 0
            if start_iso:
                start_dt = dt.datetime.fromisoformat(start_iso)
                session_seconds = int((leave_time - start_dt).total_seconds())
            self.store.add_session_time(member.id)
            self.store.state["sessions"].pop(uid, None)
            self.store.save()

            if before.channel and len([m for m in before.channel.members if not m.bot]) == 0:
                self.channel_active = False

            # === [진단] 일정 데이터 확인 ===
            has_schedules = hasattr(self.bot, 'active_schedules')
            is_target = has_schedules and (member.id in self.bot.active_schedules)
            
            if not has_schedules:
                print("[DEBUG] ❌ bot.active_schedules 속성이 없습니다. (NotionWatcher 로드 문제)")
            elif not is_target:
                # 현재 로드된 일정 대상자 목록 출력
                print(f"[DEBUG] ❌ {member.display_name} 님은 현재 일정 대상자가 아닙니다.")
                print(f"[DEBUG] 현재 인식된 일정 대상자 ID 목록: {list(self.bot.active_schedules.keys())}")
            else:
                print(f"[DEBUG] ✅ {member.display_name} 님의 일정이 확인되었습니다. 감시 프로세스 시작.")

            # --- [기능 1] 목표 초과 달성 칭찬 ---
            if is_target:
                # (기존 칭찬 로직 유지)
                pass 

            # --- [기능 2] 조기 퇴장 감지 프로세스 (수정됨) ---
            if is_target:
                sched_info = self.bot.active_schedules[member.id]
                scheduled_end = sched_info["end"]
                
                # 1단계: 60초 대기 및 1차 경고
                print(f"[DEBUG] 1분 대기 시작...")
                await asyncio.sleep(60)

                # 복귀 확인 1
                current_member = member.guild.get_member(member.id)
                is_back = False
                if current_member and current_member.voice and current_member.voice.channel:
                    if current_member.voice.channel.id == target_id:
                        is_back = True
                
                if is_back:
                    print(f"[DEBUG] 1분 내 복귀 확인됨. 알람 취소.")
                    return

                # 미복귀 시 1차 알람
                now = now_kst()
                if now < scheduled_end:
                    time_diff = scheduled_end - now
                    minutes_left = int(time_diff.total_seconds() / 60)
                    
                    if minutes_left > 1:
                        print(f"[DEBUG] 1분 미복귀 알람 전송")
                        alarm_ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                        if alarm_ch:
                            msg = (
                                f"🚨 **{member.mention} 님, 어디 가시나요?**\n"
                                f"아직 일정이 **{minutes_left}분** 남았습니다! 얼른 돌아오세요!\n"
                                f"목표 시간: {scheduled_end.strftime('%H:%M')}"
                            )
                            await alarm_ch.send(msg)
                
                # 2단계: 나머지 9분 대기 및 최종 처분
                print(f"[DEBUG] 추가 9분 대기 시작...")
                await asyncio.sleep(540) # 540초 = 9분

                # 복귀 확인 2
                current_member = member.guild.get_member(member.id)
                is_back_final = False
                if current_member and current_member.voice and current_member.voice.channel:
                    if current_member.voice.channel.id == target_id:
                        is_back_final = True
                
                if is_back_final:
                    print(f"[DEBUG] 10분 내 복귀 확인됨. 수정 취소.")
                    return

                # 최종 미복귀 처리
                if leave_time < scheduled_end:
                    print(f"[DEBUG] 10분 미복귀. 노션 수정 및 알람.")
                    await self._update_notion_end_time(sched_info["page_id"], sched_info["start"].isoformat(), leave_time.isoformat())

                    alarm_ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                    if alarm_ch:
                        msg = (
                            f"⚠️ **{member.mention} 님, 10분 넘게 돌아오지 않으셨습니다.**\n"
                            f"노션의 일정을 실제 퇴장 시간({leave_time.strftime('%H:%M')})으로 수정하였습니다."
                        )
                        await alarm_ch.send(msg)
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
        if now.weekday() != 6: return
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