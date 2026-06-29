"""
消息持久化仓库 —— 封装所有 SQLite 操作。

替代旧 sqlite.py 中的模块级函数。
所有数据库操作通过此类的实例方法完成，换数据库只需替换实现。

线程安全：aiosqlite 自动在后台线程执行 SQL，不阻塞事件循环。
事务安全：save_batch 和 replace_session_messages 使用显式 BEGIN/COMMIT/ROLLBACK。
"""
import json
import aiosqlite
from pathlib import Path


class MessageRepository:
    """消息仓库：chat_messages + sub_sessions 表的原子操作。

    由 AppContainer 创建单例，注入到 ConversationTurn 和 MessageRouter。
    db_path 来自 settings，构造函数不依赖任何其他 ai_agent 模块。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    # ── 初始化 ──

    async def init(self) -> None:
        """建表 + 索引 + 迁移。幂等操作，多次调用安全。

        表结构：
            chat_messages: 主消息表，含 user_id / role / content / tool_calls /
                          tool_call_id / reasoning_content / turn_id
            sub_sessions:  子 AI 会话标记表（标记后不出现在历史列表）
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    reasoning_content TEXT,
                    turn_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # 迁移：旧表可能没有 turn_id 列
            try:
                await db.execute("ALTER TABLE chat_messages ADD COLUMN turn_id TEXT")
            except Exception:
                pass
            # 复合索引：按 (user_id, id DESC) 加速会话消息加载
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_id_id_desc
                ON chat_messages(user_id, id DESC)
            """)
            # turn_id 索引：加速按轮删除
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_turn_id
                ON chat_messages(turn_id)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sub_sessions (
                    session_uuid TEXT PRIMARY KEY
                )
            """)
            # session_meta：会话元数据（置顶状态、自定义标题、置顶时间）
            # 独立于 chat_messages，一个会话一行，通过 session_uuid 关联
            await db.execute("""
                CREATE TABLE IF NOT EXISTS session_meta (
                    session_uuid TEXT PRIMARY KEY,
                    pinned INTEGER DEFAULT 0,
                    pinned_at TIMESTAMP,
                    custom_title TEXT
                )
            """)
            # 清理旧 workspace 表（已废弃）
            await db.execute("DROP TABLE IF EXISTS workspaces")
            await db.commit()

    # ── 会话标记 ──

    async def mark_sub_session(self, session_uuid: str) -> None:
        """标记为子 AI 会话。

        被标记的会话不出现在 get_recent_sessions 返回值中（通过 NOT IN 子查询过滤）。
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO sub_sessions (session_uuid) VALUES (?)",
                (session_uuid,),
            )
            await db.commit()

    # ── 加载 ──

    async def load(self, user_id: str) -> list[dict]:
        """加载指定会话的全部消息，按时间正序返回。"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, role, content, tool_calls, tool_call_id,
                       reasoning_content, turn_id
                FROM (
                    SELECT id, role, content, tool_calls, tool_call_id,
                           reasoning_content, turn_id
                    FROM chat_messages
                    WHERE user_id = ?
                    ORDER BY id DESC
                )
                ORDER BY id ASC
                """,
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()

        messages = []
        for row in rows:
            tc = row["tool_calls"]
            if tc and isinstance(tc, str):
                try:
                    tc = json.loads(tc)
                except Exception:
                    pass
            messages.append({
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "tool_calls": tc,
                "tool_call_id": row["tool_call_id"],
                "reasoning_content": row["reasoning_content"],
                "turn_id": row["turn_id"],
            })
        return messages

    # ── 保存 ──

    async def save(self, msg: dict) -> int:
        """保存单条消息，立即写入 DB。

        Returns:
            自增 id，可用于后续引用。
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO chat_messages
                (user_id, role, content, tool_calls, tool_call_id, reasoning_content, turn_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg.get("user_id"),
                    msg.get("role"),
                    msg.get("content"),
                    json.dumps(msg.get("tool_calls"), ensure_ascii=False) if msg.get("tool_calls") else None,
                    msg.get("tool_call_id"),
                    msg.get("reasoning_content"),
                    msg.get("turn_id"),
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def update_tool_result(self, tool_call_id: str, content: str) -> None:
        """更新指定 tool_call_id 的 tool 消息内容（placeholder → 真实结果/aborted）。

        配合 _save_tool_placeholders 使用：先 INSERT placeholder，工具完成后 UPDATE。
        按 (tool_call_id, role='tool') 定位，确保精确更新。
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE chat_messages SET content = ? WHERE tool_call_id = ? AND role = 'tool'",
                (content, tool_call_id),
            )
            await db.commit()

    async def save_batch(self, messages: list[dict]) -> None:
        """批量保存消息（显式事务，保证原子性）。

        用于工具调用结果批量入库：助手消息 + N 条工具结果要么全成功要么全回滚。
        """
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("BEGIN")
                data = [
                    (
                        msg.get("user_id"),
                        msg.get("role"),
                        msg.get("content"),
                        json.dumps(msg.get("tool_calls"), ensure_ascii=False) if msg.get("tool_calls") else None,
                        msg.get("tool_call_id"),
                        msg.get("reasoning_content"),
                        msg.get("turn_id"),
                    )
                    for msg in messages
                ]
                await db.executemany(
                    """INSERT INTO chat_messages
                    (user_id, role, content, tool_calls, tool_call_id, reasoning_content, turn_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    data,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    # ── 查询 ──

    async def get_recent_sessions(self, limit: int = 200) -> list[dict]:
        """获取最近有消息的会话列表（过滤子 AI 会话），保留旧签名供兼容。

        新代码应使用 get_pinned_sessions() + get_unpinned_sessions()。
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    m.user_id,
                    MAX(m.created_at) as last_time,
                    (SELECT content FROM chat_messages
                     WHERE user_id = m.user_id AND role = 'user'
                     ORDER BY id ASC LIMIT 1) as title,
                    sm.custom_title,
                    sm.pinned
                FROM chat_messages m
                LEFT JOIN session_meta sm ON m.user_id = sm.session_uuid
                WHERE m.user_id NOT IN (SELECT session_uuid FROM sub_sessions)
                GROUP BY m.user_id
                ORDER BY last_time DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {"user_id": row["user_id"], "last_time": row["last_time"], "title": row["title"],
             "custom_title": row["custom_title"], "pinned": bool(row["pinned"])}
            for row in rows
        ]

    # ── 会话元数据 ──

    async def get_pinned_sessions(self) -> list[dict]:
        """获取所有置顶会话，按 pinned_at 升序排列（先置顶的在前）。

        返回字段与 get_unpinned_sessions 一致，附带 custom_title 和 pinned_at。
        只返回在 chat_messages 中有消息且未被标记为 sub_session 的会话。
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    m.user_id,
                    MAX(m.created_at) as last_time,
                    (SELECT content FROM chat_messages
                     WHERE user_id = m.user_id AND role = 'user'
                     ORDER BY id ASC LIMIT 1) as title,
                    sm.custom_title,
                    sm.pinned_at
                FROM chat_messages m
                JOIN session_meta sm ON m.user_id = sm.session_uuid
                WHERE sm.pinned = 1
                  AND m.user_id NOT IN (SELECT session_uuid FROM sub_sessions)
                GROUP BY m.user_id
                ORDER BY sm.pinned_at ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {"user_id": row["user_id"], "last_time": row["last_time"], "title": row["title"],
             "custom_title": row["custom_title"], "pinned": True}
            for row in rows
        ]

    async def get_unpinned_sessions(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], bool]:
        """获取非置顶会话列表（分页），按最近活跃时间降序。

        Returns:
            (sessions, has_more): sessions 列表和是否还有更多数据。
            多查一条（limit+1）来判断 has_more，返回时去掉多余那条。
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # 多取一条判断 has_more
            async with db.execute(
                """
                SELECT
                    m.user_id,
                    MAX(m.created_at) as last_time,
                    (SELECT content FROM chat_messages
                     WHERE user_id = m.user_id AND role = 'user'
                     ORDER BY id ASC LIMIT 1) as title,
                    sm.custom_title
                FROM chat_messages m
                LEFT JOIN session_meta sm ON m.user_id = sm.session_uuid
                WHERE m.user_id NOT IN (SELECT session_uuid FROM sub_sessions)
                  AND (sm.pinned IS NULL OR sm.pinned = 0)
                GROUP BY m.user_id
                ORDER BY last_time DESC
                LIMIT ? OFFSET ?
                """,
                (limit + 1, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        sessions = [
            {"user_id": row["user_id"], "last_time": row["last_time"], "title": row["title"],
             "custom_title": row["custom_title"], "pinned": False}
            for row in rows
        ]
        return sessions, has_more

    async def set_pinned(self, session_uuid: str, pinned: bool) -> None:
        """设置或取消会话置顶。

        置顶时更新 pinned_at 为当前时间（用于排序）；
        取消置顶时仅设置 pinned=0，保留 pinned_at 以便重新置顶时刷新。
        """
        async with aiosqlite.connect(self.db_path) as db:
            if pinned:
                await db.execute(
                    """INSERT INTO session_meta (session_uuid, pinned, pinned_at)
                       VALUES (?, 1, CURRENT_TIMESTAMP)
                       ON CONFLICT(session_uuid) DO UPDATE SET
                       pinned = 1, pinned_at = CURRENT_TIMESTAMP""",
                    (session_uuid,),
                )
            else:
                await db.execute(
                    """INSERT INTO session_meta (session_uuid, pinned)
                       VALUES (?, 0)
                       ON CONFLICT(session_uuid) DO UPDATE SET pinned = 0""",
                    (session_uuid,),
                )
            await db.commit()

    async def set_custom_title(self, session_uuid: str, title: str) -> None:
        """设置会话的自定义标题。

        空标题视为清除自定义标题，后续展示回退到第一条用户消息。
        """
        async with aiosqlite.connect(self.db_path) as db:
            if title.strip():
                await db.execute(
                    """INSERT INTO session_meta (session_uuid, custom_title)
                       VALUES (?, ?)
                       ON CONFLICT(session_uuid) DO UPDATE SET custom_title = ?""",
                    (session_uuid, title.strip(), title.strip()),
                )
            else:
                # 空标题 → 清除 custom_title，回退到默认行为
                await db.execute(
                    """UPDATE session_meta SET custom_title = NULL WHERE session_uuid = ?""",
                    (session_uuid,),
                )
            await db.commit()

    # ── 删除 ──

    async def delete_session(self, user_id: str) -> None:
        """删除整个会话的所有消息 + 子会话标记 + 元数据。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM sub_sessions WHERE session_uuid = ?", (user_id,))
            # 清理会话元数据（置顶状态、自定义标题）
            await db.execute("DELETE FROM session_meta WHERE session_uuid = ?", (user_id,))
            await db.commit()

    async def delete_turn(self, user_id: str, turn_id: str) -> None:
        """删除指定会话中某一轮的所有消息（用户消息 + AI 回复 + 工具调用）。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM chat_messages WHERE user_id = ? AND turn_id = ?",
                (user_id, turn_id),
            )
            await db.commit()

    # ── 替换 ──

    async def replace_session_messages(self, user_id: str, messages: list[dict]) -> None:
        """原子替换指定会话的全部消息。

        先 DELETE 再批量 INSERT，在同一个事务中完成。
        用于上下文压缩：删除旧消息，写入压缩后的精简消息。
        """
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("BEGIN")
                await db.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))
                data = [
                    (
                        msg.get("user_id") or user_id,
                        msg.get("role"),
                        msg.get("content"),
                        json.dumps(msg.get("tool_calls"), ensure_ascii=False) if msg.get("tool_calls") else None,
                        msg.get("tool_call_id"),
                        msg.get("reasoning_content"),
                        msg.get("turn_id"),
                    )
                    for msg in messages
                ]
                await db.executemany(
                    """INSERT INTO chat_messages
                    (user_id, role, content, tool_calls, tool_call_id, reasoning_content, turn_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    data,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
