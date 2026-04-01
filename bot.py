# bot.py
import discord
import subprocess  # [추가] 깃 명령어 실행용
from discord.ext import commands
from config import REPORT_CHANNEL_ID_FEATURE

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# [추가] 최신 커밋 정보를 가져오는 함수
def get_git_commit_info():
    try:
        # 최신 커밋 메시지 가져오기
        msg = subprocess.check_output(['git', 'log', '-1', '--pretty=%s'], encoding='utf-8').strip()
        # 최신 커밋 해시(짧게) 가져오기
        sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], encoding='utf-8').strip()
        # 작성자 가져오기
        author = subprocess.check_output(['git', 'log', '-1', '--pretty=%an'], encoding='utf-8').strip()
        
        return f"{msg} (`{sha}` by {author})"
    except Exception as e:
        print(f"[WARNING] 커밋 정보 가져오기 실패: {e}")
        return "커밋 정보를 불러올 수 없습니다."

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")

    try:
        synced = await bot.tree.sync()
        print(f"[DEBUG] slash commands synced: {len(synced)}")
    except Exception as e:
        print(f"[DEBUG] slash sync error: {e}")

    # ---------------------------------------------------------
    # 배포 완료 알림 (커밋 정보 포함)
    # ---------------------------------------------------------
    if REPORT_CHANNEL_ID_FEATURE:
        try:
            channel = bot.get_channel(REPORT_CHANNEL_ID_FEATURE)
            if not channel:
                channel = await bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
            
            if channel:
                # 커밋 정보 조회
                commit_info = get_git_commit_info()
                
                embed = discord.Embed(
                    title="🚀 배포 완료!",
                    description="봇이 업데이트되어 재시작되었습니다.",
                    color=discord.Color.green()
                )
                embed.add_field(name="최신 커밋 내용", value=commit_info, inline=False)
                embed.set_footer(text=f"버전: {bot.user.name} | 현재 시간 정상 작동 중")
                
                await channel.send(embed=embed)
                
        except Exception as e:
            print(f"[ERROR] 배포 알림 전송 실패: {e}")
            
