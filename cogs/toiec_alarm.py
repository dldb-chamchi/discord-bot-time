import discord
from discord.ext import tasks, commands
import datetime as dt
import os

# 한국 시간(KST) 설정을 위한 타임존 정의
KST = dt.timezone(dt.timedelta(hours=9))

class ToeicAlarm(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 환경 변수나 config에서 채널 ID를 가져옵니다.
        self.channel_id = int(os.getenv("REPORT_CHANNEL_ID_TOEIC", "0"))
        self.toeic_task.start()

    def cog_unload(self):
        self.toeic_task.cancel()

    # 매일 밤 22시 00분(KST)에 체크하는 루프
    @tasks.loop(time=dt.time(hour=22, minute=0, tzinfo=KST))
    async def toeic_task(self):
        now = dt.datetime.now(tz=KST)
        
        # 월(0), 수(2), 토(5) 요일인지 확인
        if now.weekday() in [0, 2, 5]:
            channel = self.bot.get_channel(self.channel_id)
            if channel:
                message = "🔥 토익 인증~ 12시 전까지 노션에다가 인증 올리기!🔥"
                await channel.send(message)
                print(f"[ALARM] 토익 알림 전송 완료 (요일: {now.weekday()})")
            else:
                print(f"[ERROR] 토익 알림 채널 ID({self.channel_id})를 찾을 수 없습니다.")

async def setup(bot):
    await bot.add_cog(ToeicAlarm(bot))