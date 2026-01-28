# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / ".env"
print(f"[DEBUG] loading env from {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0"))
REPORT_CHANNEL_ID_ENTER = int(os.getenv("REPORT_CHANNEL_ID_ENTER", "0"))
REPORT_CHANNEL_ID_TOEIC = int(os.getenv("REPORT_CHANNEL_ID_TOEIC", "0"))
DATA_FILE = os.getenv("DATA_FILE", "voice_time.json")
MENTION_CHANNEL_ID = int(os.getenv("MENTION_CHANNEL_ID", "0"))
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_FEATURE_ID = os.getenv("NOTION_DATABASE_FEATURE_ID", "")
REPORT_CHANNEL_ID_FEATURE = int(os.getenv("REPORT_CHANNEL_ID_FEATURE", "0"))

NOTION_DATABASE_BOARD_ID = os.getenv("NOTION_DATABASE_BOARD_ID", "")
REPORT_CHANNEL_ID_ALARM = int(os.getenv("REPORT_CHANNEL_ID_ALARM", "0"))
NOTION_DATABASE_SCHEDULE_ID = os.getenv("NOTION_DATABASE_SCHEDULE_ID", "")
NOTION_DATABASE_TOEIC_ID = os.getenv("NOTION_DATABASE_TOEIC_ID", "")
REPORT_CHANNEL_ID_DAILY = int(os.getenv("REPORT_CHANNEL_ID_DAILY", "0"))
REPORT_CHANNEL_ID_CHASE = int(os.getenv("REPORT_CHANNEL_ID_CHASE", "0"))

if not DISCORD_TOKEN:
    raise SystemExit("DISCORD_TOKEN 환경변수를 설정하세요 (.env 사용 가능).")
if not VOICE_CHANNEL_ID or not REPORT_CHANNEL_ID_ENTER or not REPORT_CHANNEL_ID_TOEIC:
    raise SystemExit("VOICE_CHANNEL_ID / REPORT_CHANNEL_ID 환경변수를 설정하세요.")
