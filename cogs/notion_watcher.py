# cogs/notion_watcher.py
import asyncio
import aiohttp
import json
import os
from typing import Dict, Set, List, Optional, Any

from discord.ext import commands, tasks

from config import (
    NOTION_TOKEN,
    NOTION_DATABASE_FEATURE_ID,
    NOTION_DATABASE_BOARD_ID,
    REPORT_CHANNEL_ID_FEATURE,
    REPORT_CHANNEL_ID_ALARM,
)


def _is_completed_status(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    return ("완료" in n) or (n in {"done", "completed", "complete"})


def _any_completed(status_names: List[str]) -> bool:
    return any(_is_completed_status(n) for n in status_names)


def _clean_env(val: Optional[str]) -> str:
    return str(val).strip() if val else ""


class NotionWatcherCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_file = "data/notion_db.json"

        self.last_notion_row_ids: Set[str] = set()
        self.last_feature_status_by_id: Dict[str, str] = {}
        self.last_board_row_ids: Set[str] = set()

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
            print(f"[NOTION] {self.db_file} 로드 완료.")
        except Exception as e:
            print(f"[NOTION] 로드 중 오류: {e}")

    def save_state(self):
        data = {
            "features": list(self.last_notion_row_ids),
            "feature_statuses": self.last_feature_status_by_id,
            "boards": list(self.last_board_row_ids),
        }
        try:
            os.makedirs(os.path.dirname(self.db_file), exist_ok=True)
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[NOTION] 저장 중 오류: {e}")

    async def cog_load(self) -> None:
        if NOTION_TOKEN and (NOTION_DATABASE_FEATURE_ID or NOTION_DATABASE_BOARD_ID):
            self.notion_update_poller.start()
        else:
            print("[NOTION] 설정 부족으로 폴링 안 함")

    def cog_unload(self) -> None:
        if self.notion_update_poller.is_running():
            self.notion_update_poller.cancel()

    async def _send_long_message(self, channel, header, lines):
        if not lines:
            return
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
        if not clean_db_id:
            return []
        db_label = clean_db_id[-8:] if len(clean_db_id) > 8 else clean_db_id
        url = f"https://api.notion.com/v1/databases/{clean_db_id}/query"
        headers = {
            "Authorization": f"Bearer {_clean_env(NOTION_TOKEN)}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        payload = {"page_size": 50, "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]}
        try:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"[NOTION] DB 조회 실패 db={db_label} status={resp.status} body={text[:500]}")
                    return []
                data = await resp.json()
                return data.get("results", [])
        except Exception as e:
            print(f"[NOTION] DB 조회 예외 db={db_label}: {e}")
            return []

    @tasks.loop(seconds=60)
    async def notion_update_poller(self):
        if not NOTION_TOKEN:
            return
        try:
            async with aiohttp.ClientSession() as session:
                if NOTION_DATABASE_FEATURE_ID:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_FEATURE_ID)
                    new_row_ids = {row["id"] for row in rows}
                    only_new = new_row_ids - self.last_notion_row_ids
                    print(
                        "[NOTION] 기능 DB 폴링 "
                        f"stored={len(self.last_notion_row_ids)} "
                        f"fetched={len(new_row_ids)} "
                        f"new={len(only_new)} "
                        f"statuses={len(self.last_feature_status_by_id)}"
                    )

                    if only_new:
                        await asyncio.sleep(20)
                        rows = await self._fetch_notion_db(session, NOTION_DATABASE_FEATURE_ID)
                        print(
                            "[NOTION] 기능 DB 신규 감지 후 재조회 "
                            f"stored={len(self.last_notion_row_ids)} "
                            f"fetched={len(rows)} "
                            f"new={len(only_new)}"
                        )

                    if only_new:
                        new_req, new_comp = [], []
                        for row in rows:
                            if row["id"] not in only_new:
                                continue
                            props = row.get("properties", {})
                            status_names = []
                            st = props.get("상태") or next(
                                (
                                    v
                                    for v in props.values()
                                    if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select")
                                ),
                                None,
                            )
                            if st:
                                if st["type"] == "status":
                                    status_names.append(st["status"]["name"])
                                elif st["type"] == "select":
                                    status_names.append(st["select"]["name"])
                                elif st["type"] == "multi_select":
                                    status_names.extend(o["name"] for o in st["multi_select"])

                            c_txt = "".join(
                                x["plain_text"]
                                for x in (props.get("내용", {}).get("title") or props.get("내용", {}).get("rich_text") or [])
                            ) or "(내용 없음)"
                            d_txt = "".join(
                                x["plain_text"]
                                for x in (props.get("설명", {}).get("rich_text") or props.get("Description", {}).get("rich_text") or [])
                            ) or "(설명 없음)"
                            line = f"- {c_txt} — {d_txt}"

                            if _any_completed(status_names):
                                new_comp.append(line)
                            else:
                                new_req.append(line)
                            self.last_feature_status_by_id[row["id"]] = ",".join(status_names)

                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        await self._send_long_message(ch, "기능 요청이 들어왔습니다 ✨", new_req)
                        await self._send_long_message(ch, "기능이 추가됐습니다 ✅", new_comp)

                    st_change = []
                    for row in rows:
                        props = row.get("properties", {})
                        status_names = []
                        st = props.get("상태") or next(
                            (
                                v
                                for v in props.values()
                                if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select")
                            ),
                            None,
                        )
                        if st:
                            if st["type"] == "status":
                                status_names.append(st["status"]["name"])
                            elif st["type"] == "select":
                                status_names.append(st["select"]["name"])
                            elif st["type"] == "multi_select":
                                status_names.extend(o["name"] for o in st["multi_select"])

                        prev = self.last_feature_status_by_id.get(row["id"])
                        if prev is not None:
                            prev_c = _any_completed([p.strip() for p in prev.split(",")])
                            curr_c = _any_completed(status_names)
                            if curr_c and not prev_c:
                                c_txt = "".join(
                                    x["plain_text"]
                                    for x in (props.get("내용", {}).get("title") or props.get("내용", {}).get("rich_text") or [])
                                ) or "(내용 없음)"
                                d_txt = "".join(
                                    x["plain_text"]
                                    for x in (props.get("설명", {}).get("rich_text") or props.get("Description", {}).get("rich_text") or [])
                                ) or "(설명 없음)"
                                st_change.append(f"- {c_txt} — {d_txt}")
                        self.last_feature_status_by_id[row["id"]] = ",".join(status_names)

                    if st_change:
                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        await self._send_long_message(ch, "기능이 추가됐습니다 ✅", st_change)

                    self.last_notion_row_ids = new_row_ids
                    print(
                        "[NOTION] 기능 DB 상태 저장 "
                        f"features={len(self.last_notion_row_ids)} "
                        f"statuses={len(self.last_feature_status_by_id)}"
                    )
                    self.save_state()

                if NOTION_DATABASE_BOARD_ID and REPORT_CHANNEL_ID_ALARM:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_BOARD_ID)
                    ids = {r["id"] for r in rows}
                    print(
                        "[NOTION] 게시판 DB 폴링 "
                        f"stored={len(self.last_board_row_ids)} "
                        f"fetched={len(ids)} "
                        f"new={len(ids - self.last_board_row_ids)}"
                    )
                    if ids - self.last_board_row_ids:
                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                        await ch.send("게시판에 새로운 글이 올라왔습니다.")
                        self.last_board_row_ids = ids
                        print(f"[NOTION] 게시판 DB 상태 저장 boards={len(self.last_board_row_ids)}")
                        self.save_state()

        except Exception as e:
            print(f"[NOTION] Error: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(NotionWatcherCog(bot))
