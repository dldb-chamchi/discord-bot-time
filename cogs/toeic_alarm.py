import discord
from discord.ext import tasks, commands
import datetime as dt
import aiohttp
from discord.utils import get
from config import NOTION_TOKEN, NOTION_DATABASE_TOEIC_ID, REPORT_CHANNEL_ID_TOEIC

KST = dt.timezone(dt.timedelta(hours=9))

class ToeicAlarm(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.name_mapping = {
            "임아리": "이유",
            "김성아": "SAK",
            "장민지": "민둥"
        }
        self.general_notice.start() # 22시 일반 알람
        self.check_and_ping.start() # 23시 정밀 점검

    def cog_unload(self):
        self.general_notice.cancel()
        self.check_and_ping.cancel()

    # 1. 밤 10시: 전체 리마인드 공지 (기존 기능)
    @tasks.loop(time=dt.time(hour=22, minute=0, tzinfo=KST))
    async def general_notice(self):
        now = dt.datetime.now(tz=KST)
        if now.weekday() in [0, 2, 5]: # 월, 수, 토
            channel = self.bot.get_channel(REPORT_CHANNEL_ID_TOEIC) or \
                      await self.bot.fetch_channel(REPORT_CHANNEL_ID_TOEIC)
            if channel:
                await channel.send("🔥 토익 인증~ 12시 전까지 노션에다가 인증 올리기!🔥")

    # 2. 밤 11시: 노션 확인 후 미인증자만 멘션 (새 기능)
    @tasks.loop(time=dt.time(hour=23, minute=0, tzinfo=KST))
    async def check_and_ping(self):
        now = dt.datetime.now(tz=KST)
        if now.weekday() not in [0, 2, 5]: return

        target_str = (now + dt.timedelta(days=1)).strftime("%Y.%m.%d")
        
        async with aiohttp.ClientSession() as session:
            # 노션에서 내일 날짜 페이지 찾기
            page = await self._fetch_page(session, target_str)
            if not page:
                return # 페이지 없으면 중단

            props = page.get("properties", {})
            missing_users = []

            for n_name, d_name in self.name_mapping.items():
                p = props.get(n_name, {})
                # Relation이 비어있는지 확인
                if p.get("type") == "relation" and not p.get("relation"):
                    m = self._find_member(d_name)
                    missing_users.append(m.mention if m else f"@{d_name}")

            if missing_users:
                ch = self.bot.get_channel(REPORT_CHANNEL_ID_TOEIC) or \
                     await self.bot.fetch_channel(REPORT_CHANNEL_ID_TOEIC)
                await ch.send(f"🔔 {' '.join(missing_users)}\n내일({target_str})자 인증 페이지가 비어있습니다! 확인해 주세요! 🔥")

    async def _fetch_page(self, session, date_str):
        url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_TOEIC_ID}/query"
        headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        payload = {"filter": {"property": "이름", "title": {"equals": date_str}}}
        async with session.post(url, headers=headers, json=payload) as r:
            if r.status == 200:
                res = await r.json()
                return res["results"][0] if res["results"] else None
        return None

    def _find_member(self, name):
        for g in self.bot.guilds:
            member = get(g.members, display_name=name) or get(g.members, name=name)
            if member: return member
        return None

async def setup(bot):
    await bot.add_cog(ToeicAlarm(bot))