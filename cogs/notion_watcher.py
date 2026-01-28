# cogs/notion_watcher.py
import asyncio
import aiohttp
import json
import os
import datetime as dt
from typing import Dict, Set, List, Optional, Any

from discord.ext import commands, tasks
from discord.utils import get

from config import (
    NOTION_TOKEN,
    NOTION_DATABASE_FEATURE_ID,
    NOTION_DATABASE_BOARD_ID,
    NOTION_DATABASE_SCHEDULE_ID,
    REPORT_CHANNEL_ID_FEATURE,
    REPORT_CHANNEL_ID_ALARM,
    VOICE_CHANNEL_ID, # [추가] 공부 채널 ID 필요
)
from time_utils import now_kst, KST

# ===== 헬퍼 함수들 =====

def _is_completed_status(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    return ("완료" in n) or (n in {"done", "completed", "complete"})

def _any_completed(status_names: List[str]) -> bool:
    return any(_is_completed_status(n) for n in status_names)

def _trim_to_minute(iso_str: str) -> str:
    if not iso_str:
        return ""
    if "T" in iso_str:
        date_part, time_part = iso_str.split("T", 1)
        hhmm = time_part[:5]
        return f"{date_part} {hhmm}"
    return iso_str

def _clean_env(val: Optional[str]) -> str:
    return str(val).strip() if val else ""

class NotionWatcherCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_file = "data/notion_db.json"
        
        self.last_notion_row_ids: Set[str] = set()
        self.last_feature_status_by_id: Dict[str, str] = {}
        self.last_board_row_ids: Set[str] = set()
        self.last_schedule_row_ids: Set[str] = set()

        self.load_state()

    def load_state(self):
        if not os.path.exists(self.db_file) or os.path.getsize(self.db_file) == 0:
            print(f"[NOTION] {self.db_file} 파일이 없거나 비어 있어 새로 시작합니다.")
            return
        try:
            with open(self.db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.last_notion_row_ids = set(data.get("features", []))
                self.last_feature_status_by_id = data.get("feature_statuses", {})
                self.last_board_row_ids = set(data.get("boards", []))
                self.last_schedule_row_ids = set(data.get("schedules", []))
            print(f"[NOTION] {self.db_file} 로드 완료.")
        except Exception as e:
            print(f"[NOTION] 로드 중 오류: {e}")

    def save_state(self):
        data = {
            "features": list(self.last_notion_row_ids),
            "feature_statuses": self.last_feature_status_by_id,
            "boards": list(self.last_board_row_ids),
            "schedules": list(self.last_schedule_row_ids)
        }
        try:
            os.makedirs(os.path.dirname(self.db_file), exist_ok=True)
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[NOTION] 저장 중 오류: {e}")

    async def cog_load(self) -> None:
        if NOTION_TOKEN and (NOTION_DATABASE_FEATURE_ID or NOTION_DATABASE_SCHEDULE_ID):
            self.notion_update_poller.start()
        else:
            print("[NOTION] 설정 부족으로 폴링 안 함")

    def cog_unload(self) -> None:
        if self.notion_update_poller.is_running():
            self.notion_update_poller.cancel()

    async def _send_long_message(self, channel, header, lines):
        if not lines: return
        current_message = (header + "\n") if header else ""
        for line in lines:
            if len(current_message) + len(line) > 1900:
                await channel.send(current_message)
                current_message = ""
            current_message += line + "\n"
        if current_message:
            await channel.send(current_message)

    async def _fetch_notion_db(self, session: aiohttp.ClientSession, db_id: str) -> List[Dict[str, Any]]:
        clean_db_id = _clean_env(db_id)
        if not clean_db_id: return []
        url = f"https://api.notion.com/v1/databases/{clean_db_id}/query"
        headers = {
            "Authorization": f"Bearer {_clean_env(NOTION_TOKEN)}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        payload = {"page_size": 50, "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]}
        try:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200: return []
                data = await resp.json()
                return data.get("results", [])
        except Exception: return []

    async def _update_active_schedules(self, session: aiohttp.ClientSession):
        if not NOTION_DATABASE_SCHEDULE_ID: return

        NAME_MAPPING = {"임아리": "이유", "김성아": "SAK", "장민지": "민둥"}
        today_str = now_kst().strftime("%Y-%m-%d")
        url = f"https://api.notion.com/v1/databases/{str(NOTION_DATABASE_SCHEDULE_ID).strip()}/query"
        headers = {
            "Authorization": f"Bearer {str(NOTION_TOKEN).strip()}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        payload = {"filter": {"property": "날짜", "date": {"on_or_after": today_str}}}

        try:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200: return
                data = await resp.json()
                results = data.get("results", [])
                new_schedules = {}
                now = now_kst()

                for row in results:
                    props = row.get("properties", {})
                    date_prop = props.get("날짜", {}).get("date", {})
                    if not date_prop or not date_prop.get("end"): continue
                    
                    try:
                        start_dt = dt.datetime.fromisoformat(date_prop["start"])
                        if start_dt.tzinfo is None: start_dt = start_dt.replace(tzinfo=KST)
                        end_dt = dt.datetime.fromisoformat(date_prop["end"])
                        if end_dt.tzinfo is None: end_dt = end_dt.replace(tzinfo=KST)
                    except: continue

                    # [수정됨] 디스코드 멤버 찾기를 시간 체크보다 먼저 수행
                    raw_names = [opt["name"] for opt in props.get("태그", {}).get("multi_select", [])]
                    target_member = None
                    target_notion_name = ""

                    for raw_name in raw_names:
                        target_name = NAME_MAPPING.get(raw_name, raw_name)
                        for guild in self.bot.guilds:
                            member = get(guild.members, display_name=target_name) or get(guild.members, name=target_name)
                            if member:
                                target_member = member
                                target_notion_name = raw_name
                                break
                        if target_member: break
                    
                    if not target_member:
                        continue

                    # [수정됨] 유지 조건 확인
                    # 1. 시간이 아직 안 끝났거나, 끝난지 30분 이내 (여유 시간)
                    buffer_time = dt.timedelta(minutes=30)
                    is_time_remaining = (end_dt + buffer_time) >= now
                    
                    # 2. 시간은 지났지만 현재 공부 채널에 접속 중임 (초과 달성 중)
                    is_in_voice = False
                    if target_member.voice and target_member.voice.channel and target_member.voice.channel.id == VOICE_CHANNEL_ID:
                        is_in_voice = True

                    # 시작은 했어야 함
                    is_started = now >= start_dt

                    # (시작됨) AND (시간남음 OR 공부중) 이면 삭제하지 않고 유지
                    if is_started and (is_time_remaining or is_in_voice):
                        new_schedules[target_member.id] = {
                            "end": end_dt,
                            "page_id": row["id"],
                            "start": start_dt,
                            "name": target_notion_name
                        }
                    
                self.bot.active_schedules = new_schedules
        except Exception as e:
            print(f"[NOTION] Schedule Update Error: {e}")

    @tasks.loop(seconds=60)
    async def notion_update_poller(self):
        if not NOTION_TOKEN: return
        try:
            async with aiohttp.ClientSession() as session:
                await self._update_active_schedules(session)

                if NOTION_DATABASE_FEATURE_ID:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_FEATURE_ID)
                    new_row_ids = {row["id"] for row in rows}
                    only_new = new_row_ids - self.last_notion_row_ids
                    
                    if only_new:
                        await asyncio.sleep(20)
                        rows = await self._fetch_notion_db(session, NOTION_DATABASE_FEATURE_ID)

                    if only_new:
                        new_req, new_comp = [], []
                        for row in rows:
                            if row["id"] not in only_new: continue
                            props = row.get("properties", {})
                            status_names = []
                            st = props.get("상태") or next((v for v in props.values() if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select")), None)
                            if st:
                                if st["type"] == "status": status_names.append(st["status"]["name"])
                                elif st["type"] == "select": status_names.append(st["select"]["name"])
                                elif st["type"] == "multi_select": status_names.extend(o["name"] for o in st["multi_select"])

                            c_txt = "".join(x["plain_text"] for x in (props.get("내용", {}).get("title") or props.get("내용", {}).get("rich_text") or [])) or "(내용 없음)"
                            d_txt = "".join(x["plain_text"] for x in (props.get("설명", {}).get("rich_text") or props.get("Description", {}).get("rich_text") or [])) or "(설명 없음)"
                            line = f"- {c_txt} — {d_txt}"
                            
                            if _any_completed(status_names): new_comp.append(line)
                            else: new_req.append(line)
                            self.last_feature_status_by_id[row["id"]] = ",".join(status_names)

                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        await self._send_long_message(ch, "기능 요청이 들어왔습니다 ✨", new_req)
                        await self._send_long_message(ch, "기능이 추가됐습니다 ✅", new_comp)

                    st_change = []
                    for row in rows:
                        props = row.get("properties", {})
                        status_names = []
                        st = props.get("상태") or next((v for v in props.values() if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select")), None)
                        if st:
                            if st["type"] == "status": status_names.append(st["status"]["name"])
                            elif st["type"] == "select": status_names.append(st["select"]["name"])
                            elif st["type"] == "multi_select": status_names.extend(o["name"] for o in st["multi_select"])

                        prev = self.last_feature_status_by_id.get(row["id"])
                        if prev is not None:
                            prev_c = _any_completed([p.strip() for p in prev.split(",")])
                            curr_c = _any_completed(status_names)
                            if curr_c and not prev_c:
                                c_txt = "".join(x["plain_text"] for x in (props.get("내용", {}).get("title") or props.get("내용", {}).get("rich_text") or [])) or "(내용 없음)"
                                d_txt = "".join(x["plain_text"] for x in (props.get("설명", {}).get("rich_text") or props.get("Description", {}).get("rich_text") or [])) or "(설명 없음)"
                                st_change.append(f"- {c_txt} — {d_txt}")
                        self.last_feature_status_by_id[row["id"]] = ",".join(status_names)

                    if st_change:
                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        await self._send_long_message(ch, "기능이 추가됐습니다 ✅", st_change)
                    
                    self.last_notion_row_ids = new_row_ids
                    self.save_state()

                if NOTION_DATABASE_BOARD_ID and REPORT_CHANNEL_ID_ALARM:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_BOARD_ID)
                    ids = {r["id"] for r in rows}
                    if ids - self.last_board_row_ids:
                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                        await ch.send("게시판에 새로운 글이 올라왔습니다.")
                        self.last_board_row_ids = ids
                        self.save_state()

                if NOTION_DATABASE_SCHEDULE_ID and REPORT_CHANNEL_ID_ALARM:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_SCHEDULE_ID)
                    ids = {r["id"] for r in rows}
                    new_ids = ids - self.last_schedule_row_ids
                    if new_ids:
                        await asyncio.sleep(20)
                        rows = await self._fetch_notion_db(session, NOTION_DATABASE_SCHEDULE_ID)
                        lines = []
                        for row in rows:
                            if row["id"] not in new_ids: continue
                            props = row.get("properties", {})
                            d_prop = props.get("날짜") or next((v for v in props.values() if isinstance(v, dict) and v.get("type") == "date"), None)
                            d_str = _trim_to_minute(d_prop["date"]["start"]) + (f" ~ {_trim_to_minute(d_prop['date']['end'])}" if d_prop and d_prop.get("date") and d_prop["date"].get("end") else "") if d_prop and d_prop.get("date") else ""
                            tags = [o["name"] for o in (props.get("태그", {}).get("multi_select") or [])]
                            lines.append(f"- {', '.join(tags) if tags else '(태그 없음)'} — {d_str}")
                        
                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                        await self._send_long_message(ch, "새 일정이 등록되었습니다 📅", lines)
                        self.last_schedule_row_ids = ids
                        self.save_state()

        except Exception as e:
            print(f"[NOTION] Error: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(NotionWatcherCog(bot))