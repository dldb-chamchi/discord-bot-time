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
)
from time_utils import now_kst

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
        if not os.path.exists(self.db_file):
            print(f"[NOTION] {self.db_file} 파일이 없어 새로 시작합니다.")
            return
        
        if os.path.getsize(self.db_file) == 0:
            print(f"[NOTION] {self.db_file} 파일이 비어 있어 초기화를 건너뜁니다.")
            return

        try:
            with open(self.db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.last_notion_row_ids = set(data.get("features", []))
                self.last_feature_status_by_id = data.get("feature_statuses", {})
                self.last_board_row_ids = set(data.get("boards", []))
                self.last_schedule_row_ids = set(data.get("schedules", []))
            print(f"[NOTION] {self.db_file} 로드 완료.")
        except (json.JSONDecodeError, Exception) as e:
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
        if NOTION_TOKEN and NOTION_DATABASE_FEATURE_ID:
            self.notion_update_poller.start()
        else:
            print("[NOTION] 설정 부족으로 폴링 안 함")

    def cog_unload(self) -> None:
        if self.notion_update_poller.is_running():
            self.notion_update_poller.cancel()

    # [추가] 긴 메시지를 2000자 이하로 나누어 전송하는 함수
    async def _send_long_message(self, channel, header, lines):
        """긴 메시지를 2000자 이하로 나누어 전송합니다."""
        if not lines:
            return
            
        current_message = header + "\n" if header else ""
        for line in lines:
            # 새로운 라인을 추가했을 때 1900자를 넘으면 전송
            if len(current_message) + len(line) + 1 > 1900:
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
        if not NOTION_DATABASE_SCHEDULE_ID:
            return

        NAME_MAPPING = {
            "임아리": "이유",
            "김성아": "SAK",
            "장민지": "민둥"
        }

        today_str = now_kst().strftime("%Y-%m-%d")
        clean_db_id = str(NOTION_DATABASE_SCHEDULE_ID).strip()
        url = f"https://api.notion.com/v1/databases/{clean_db_id}/query"
        headers = {
            "Authorization": f"Bearer {str(NOTION_TOKEN).strip()}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        payload = {
            "filter": {
                "property": "날짜",
                "date": {
                    "on_or_after": today_str
                }
            }
        }

        try:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200: return
                data = await resp.json()
                results = data.get("results", [])

                new_schedules = {}
                now = now_kst()

                for row in results:
                    props = row.get("properties", {})
                    date_prop = props.get("날짜", {})
                    if not date_prop: continue
                    date_data = date_prop.get("date", {})
                    if not date_data: continue
                    end_str = date_data.get("end")
                    if not end_str: continue 
                    
                    try:
                        end_dt = dt.datetime.fromisoformat(end_str)
                        if end_dt.tzinfo is None:
                             KST = dt.timezone(dt.timedelta(hours=9))
                             end_dt = end_dt.replace(tzinfo=KST)
                    except ValueError: continue

                    if end_dt < now: continue

                    tag_prop = props.get("태그", {})
                    raw_names = []
                    if tag_prop.get("type") == "multi_select":
                        raw_names = [opt["name"] for opt in tag_prop.get("multi_select", [])]
                    
                    if not raw_names: continue

                    for raw_name in raw_names:
                        target_name = NAME_MAPPING.get(raw_name, raw_name)
                        target_member = None
                        clean_target = target_name.replace(" ", "").lower()
                        
                        for guild in self.bot.guilds:
                            found = get(guild.members, display_name=target_name) or \
                                    get(guild.members, name=target_name)
                            
                            if not found:
                                for m in guild.members:
                                    if m.bot: continue
                                    d_name = (m.display_name or "").replace(" ", "").lower()
                                    r_name = (m.name or "").replace(" ", "").lower()
                                    if d_name == clean_target or r_name == clean_target:
                                        found = m
                                        break
                            
                            if found:
                                target_member = found
                                break
                        
                        if target_member:
                            current_data = new_schedules.get(target_member.id)
                            if not current_data or end_dt > current_data["end"]:
                                new_schedules[target_member.id] = {
                                    "end": end_dt,
                                    "page_id": row["id"],
                                    "start": date_data.get("start")
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
                        new_req = []
                        new_comp = []
                        for row in rows:
                            if row["id"] not in only_new: continue
                            rid = row["id"]
                            props = row.get("properties", {})
                            
                            status_names = []
                            st = props.get("상태")
                            if not st:
                                for v in props.values():
                                    if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select"): st=v; break
                            if st:
                                t = st.get("type")
                                if t == "status":
                                    n = st.get("status", {}).get("name")
                                    if n: status_names.append(n)
                                elif t == "select":
                                    n = st.get("select", {}).get("name")
                                    if n: status_names.append(n)
                                elif t == "multi_select":
                                    for o in st.get("multi_select", []):
                                        if o.get("name"): status_names.append(o.get("name"))

                            c_txt = "(내용 없음)"
                            cp = props.get("내용")
                            if cp and cp.get("type") == "title": c_txt = "".join([x.get("plain_text","") for x in cp.get("title",[])]).strip() or "(내용 없음)"
                            elif cp and cp.get("type") == "rich_text": c_txt = "".join([x.get("plain_text","") for x in cp.get("rich_text",[])]).strip() or "(내용 없음)"
                            
                            d_txt = "(설명 없음)"
                            dp = props.get("설명") or props.get("Description")
                            if dp and dp.get("type") == "rich_text": d_txt = "".join([x.get("plain_text","") for x in dp.get("rich_text",[])]).strip() or "(설명 없음)"

                            line = f"- {c_txt} — {d_txt}"
                            if _any_completed(status_names): new_comp.append(line)
                            else: new_req.append(line)
                            if status_names: self.last_feature_status_by_id[rid] = ",".join(status_names)

                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        # [수정] self._send_long_message 사용
                        if new_req: await self._send_long_message(ch, "기능 요청이 들어왔습니다 ✨", new_req)
                        if new_comp: await self._send_long_message(ch, "기능이 추가됐습니다 ✅", new_comp)

                    st_change = []
                    for row in rows:
                        rid = row["id"]
                        props = row.get("properties", {})
                        status_names = []
                        st = props.get("상태")
                        if not st:
                            for v in props.values():
                                if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select"): st=v; break
                        if st:
                            t = st.get("type")
                            if t == "status": n=st.get("status", {}).get("name"); status_names.append(n) if n else None
                            elif t == "select": n=st.get("select", {}).get("name"); status_names.append(n) if n else None
                            elif t == "multi_select": [status_names.append(o["name"]) for o in st.get("multi_select",[]) if o.get("name")]

                        prev = self.last_feature_status_by_id.get(rid)
                        if prev is None:
                            if status_names: self.last_feature_status_by_id[rid] = ",".join(status_names)
                            continue

                        prev_c = _any_completed([p.strip() for p in (prev.split(",") if prev else [])])
                        curr_c = _any_completed(status_names)

                        if curr_c and not prev_c:
                            c_txt = "(내용 없음)"
                            cp = props.get("내용")
                            if cp and cp.get("type") == "title": c_txt = "".join([x.get("plain_text","") for x in cp.get("title",[])]).strip() or "(내용 없음)"
                            elif cp and cp.get("type") == "rich_text": c_txt = "".join([x.get("plain_text","") for x in cp.get("rich_text",[])]).strip() or "(내용 없음)"
                            
                            d_txt = "(설명 없음)"
                            dp = props.get("설명") or props.get("Description")
                            if dp and dp.get("type") == "rich_text": d_txt = "".join([x.get("plain_text","") for x in dp.get("rich_text",[])]).strip() or "(설명 없음)"
                            st_change.append(f"- {c_txt} — {d_txt}")

                        if status_names: self.last_feature_status_by_id[rid] = ",".join(status_names)

                    if st_change:
                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        # [수정] self._send_long_message 사용
                        await self._send_long_message(ch, "기능이 추가됐습니다 ✅", st_change)
                    
                    if only_new or st_change or (new_row_ids != self.last_notion_row_ids):
                        self.last_notion_row_ids = new_row_ids
                        self.save_state()

                if NOTION_DATABASE_BOARD_ID and REPORT_CHANNEL_ID_ALARM:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_BOARD_ID)
                    ids = {r["id"] for r in rows}
                    new_ids = ids - self.last_board_row_ids
                    if new_ids:
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
                        ids = {r["id"] for r in rows}
                        new_ids = ids - self.last_schedule_row_ids
                        
                        if new_ids:
                            lines = ["새 일정이 등록되었습니다 📅"]
                            for row in rows:
                                if row["id"] not in new_ids: continue
                                props = row.get("properties", {})
                                d_str = ""
                                dp = props.get("날짜")
                                if not dp:
                                    for v in props.values():
                                        if isinstance(v, dict) and v.get("type") == "date": dp=v; break
                                if dp and dp.get("type")=="date":
                                    d = dp.get("date") or {}
                                    s = _trim_to_minute(d.get("start"))
                                    e = _trim_to_minute(d.get("end"))
                                    d_str = s if not e else f"{s} ~ {e}"
                                tags = []
                                tp = props.get("태그")
                                if not tp:
                                    for v in props.values():
                                        if isinstance(v, dict) and v.get("type") == "multi_select": tp=v; break
                                if tp and tp.get("type")=="multi_select":
                                    for o in tp.get("multi_select",[]):
                                        if o.get("name"): tags.append(o.get("name"))
                                t_str = ", ".join(tags) if tags else "(태그 없음)"
                                lines.append(f"- {t_str} — {d_str}" if d_str else f"- {t_str}")

                            ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                            # [수정] self._send_long_message 사용
                            await self._send_long_message(ch, "", lines)
                            
                        self.last_schedule_row_ids = ids
                        self.save_state()

        except Exception as e:
            print(f"[NOTION] Error: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(NotionWatcherCog(bot))