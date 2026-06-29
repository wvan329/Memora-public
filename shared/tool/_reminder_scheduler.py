"""
提醒调度器 —— 事件驱动 + 持久化 + 崩溃恢复。

核心设计：
- 每个提醒一个独立的 asyncio.Task，计算延迟后 sleep，到点推送手机通知。
- 不轮询，CPU 零开销。
- reminders.json 持久化，系统重启后自动恢复。
- 每日提醒（daily）：推送后自动调度到明天同一时间。
- 一次性提醒（once）：必须指定 date（YYYY-MM-DD），触发后自动删除。
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path

from ai_agent.container import get_container
from shared.tool._common import _atomic_write


class ReminderScheduler:
    """提醒调度器：管理所有提醒的异步 Task，处理持久化和推送。"""

    def __init__(self, storage_path: str):
        self._path = Path(storage_path)
        self._tasks: dict[str, asyncio.Task] = {}
        self._eye_rest_task: asyncio.Task | None = None

    # ── 远眺提醒（内置，每 N 分钟弹 macOS 系统通知）──

    def start_eye_rest(self, interval_minutes: int = 20):
        """启动远眺提醒协程。无需持久化，纯内存循环。"""
        if self._eye_rest_task is not None:
            return

        async def _loop():
            from ai_agent.platform_utils import show_desktop_popup
            while True:
                await asyncio.sleep(interval_minutes * 60)
                show_desktop_popup("远眺 20 秒，保护眼睛 👀", seconds=20)

        self._eye_rest_task = asyncio.create_task(_loop())

    def stop_eye_rest(self):
        """停止远眺提醒。"""
        if self._eye_rest_task is not None:
            self._eye_rest_task.cancel()
            self._eye_rest_task = None

    async def start(self):
        """启动调度器：读取 JSON，为每个启用的提醒创建后台 Task。"""
        reminders = self._load()
        for r in reminders:
            if r.get("enabled", True):
                self._schedule_one(r)
        count = len(self._tasks)
        if count > 0:
            print(f"[ReminderScheduler] 已加载 {count} 个提醒")

    async def add(self, reminder: dict) -> str:
        """新增提醒：验证 → 写入 JSON → 创建后台 Task。"""
        rid = reminder["id"]
        reminder.setdefault("type", "daily")
        reminder.setdefault("enabled", True)
        reminder.setdefault("last_date", None)

        try:
            hour, minute = map(int, reminder["time"].split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            return "错误：时间格式无效，应为 HH:MM，如 '15:00'"

        if reminder["type"] == "once":
            if "date" not in reminder:
                return "错误：一次性提醒（type='once'）必须提供 date 参数，格式 YYYY-MM-DD"
            try:
                target_date = datetime.strptime(reminder["date"], "%Y-%m-%d").date()
                target_dt = datetime.combine(target_date, dt_time(hour, minute))
                if target_dt <= datetime.now():
                    return f"错误：目标时间 {reminder['date']} {reminder['time']} 已过期，无法添加"
            except ValueError:
                return "错误：日期格式无效，应为 YYYY-MM-DD，如 '2026-07-14'"

        # id 已存在则拒绝，防止误覆盖
        if rid in self._tasks:
            return f"错误：提醒 ID '{rid}' 已存在，请先删除再添加，或使用不同的 ID"
        # 同时检查文件中是否有残留（如重启后 task 未恢复但文件仍有记录）
        for r in self._load():
            if r["id"] == rid:
                return f"错误：提醒 ID '{rid}' 已存在（文件中有记录），请先删除再添加，或使用不同的 ID"

        reminders = self._load()
        reminders.append(reminder)
        self._save(reminders)

        self._schedule_one(reminder)

        date_info = f" {reminder['date']}" if reminder.get("date") else ""
        return f"已添加提醒：{reminder['time']}{date_info} — {reminder['message']}"

    async def remove(self, reminder_id: str) -> str:
        """删除一个提醒：取消 Task + 从 JSON 移除。"""
        if reminder_id in self._tasks:
            self._tasks[reminder_id].cancel()
            del self._tasks[reminder_id]

        reminders = self._load()
        new_list = [r for r in reminders if r["id"] != reminder_id]
        if len(new_list) == len(reminders):
            return f"未找到提醒：{reminder_id}"
        self._save(new_list)
        return f"已删除提醒：{reminder_id}"

    def list_all(self) -> list[dict]:
        """列出所有提醒。"""
        reminders = self._load()
        for r in reminders:
            r["_active_task"] = r["id"] in self._tasks
        return reminders

    # ── 内部 ──

    def _schedule_one(self, r: dict):
        rid = r["id"]
        if rid in self._tasks:
            self._tasks[rid].cancel()
        self._tasks[rid] = asyncio.create_task(self._run_one(r))

    async def _run_one(self, r: dict):
        rid = r["id"]
        try:
            while True:
                now = datetime.now()
                today = now.date()
                hour, minute = map(int, r["time"].split(":"))

                if r["type"] == "once":
                    target_date = datetime.strptime(r["date"], "%Y-%m-%d").date()
                    target = datetime.combine(target_date, dt_time(hour, minute))
                else:
                    # daily：固定今天时间，只有今天已推过才推到明天
                    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if r.get("last_date") == today.isoformat():
                        target += timedelta(days=1)

                delay = (target - datetime.now()).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)

                # 等待手机上线
                while not get_container().mobile_pool.is_online():
                    await asyncio.sleep(120)

                await get_container().mobile_pool.broadcast({
                    "type": "push_notification",
                    "title": "Memora",
                    "content": r["message"],
                    "timestamp": time.time(),
                })

                if r["type"] == "once":
                    self._remove_from_file(rid)
                    self._tasks.pop(rid, None)
                    return
                else:
                    r["last_date"] = datetime.now().date().isoformat()
                    self._update_last_date_in_file(rid, r["last_date"])
        except asyncio.CancelledError:
            pass

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save(self, reminders: list[dict]):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._path, json.dumps(reminders, ensure_ascii=False, indent=2))

    def _remove_from_file(self, rid: str):
        reminders = self._load()
        self._save([r for r in reminders if r["id"] != rid])

    def _update_last_date_in_file(self, rid: str, date_str: str):
        reminders = self._load()
        for r in reminders:
            if r["id"] == rid:
                r["last_date"] = date_str
                break
        self._save(reminders)


# ═══════════════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════════════

_scheduler: ReminderScheduler | None = None


def get_scheduler() -> ReminderScheduler:
    if _scheduler is None:
        raise RuntimeError("ReminderScheduler 尚未初始化")
    return _scheduler


def set_scheduler(scheduler: ReminderScheduler):
    global _scheduler
    _scheduler = scheduler
