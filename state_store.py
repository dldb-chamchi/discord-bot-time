# state_store.py
import os
import json
import datetime as dt
from typing import Dict, Any

from time_utils import now_kst, parse_iso, iso

class StateStore:
    def __init__(self, data_file: str):
        self.data_file = data_file
        self.state: Dict[str, Any] = {
            "totals": {},           # user_id(str) -> 누적 초(int) [주간 리포트용]
            "sessions": {},         # user_id(str) -> 시작시각(ISO str)
            "schedule_progress": {}, # [추가] page_id(str) -> 누적 초(int) [일정별 칭찬용]
            "praised_pages": []      # [추가] page_id(str) 목록 [중복 칭찬 방지용]
        }

    def load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.state["totals"] = data.get("totals", {})
                    self.state["sessions"] = data.get("sessions", {})
                    self.state["schedule_progress"] = data.get("schedule_progress", {})
                    self.state["praised_pages"] = data.get("praised_pages", [])
            except Exception:
                pass

    def save(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False)

    def add_session_time(self, user_id: int, until: dt.datetime | None = None):
        uid = str(user_id)
        start_iso = self.state["sessions"].get(uid)
        if not start_iso:
            return 0 # 경과 시간 반환하도록 수정
        start = parse_iso(start_iso)
        end = until or now_kst()
        elapsed = int((end - start).total_seconds())
        if elapsed > 0:
            self.state["totals"][uid] = self.state["totals"].get(uid, 0) + elapsed
        return elapsed