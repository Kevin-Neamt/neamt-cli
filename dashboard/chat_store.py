from __future__ import annotations

# dashboard/chat_store.py — persistent chat sessions (Claude/ChatGPT style).
# SQLite-backed conversations + messages + groups, stored under ~/.neamt/.
# Ported from the neamt-ai dashboard; trimmed to what the official Neamt
# dashboard needs (no neuron routing — a single Claude assistant).

import sqlite3
import time
from pathlib import Path
from typing import Optional

_DB_DIR  = Path.home() / ".neamt"
_DB_PATH = _DB_DIR / "chats.db"
_RETENTION_SEC = 14 * 24 * 60 * 60  # 14 days


def _uid() -> str:
    import random, string
    t = format(int(time.time() * 1000), "x")
    r = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return t + r


def _row(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class ChatStore:
    def __init__(self) -> None:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._schema()
        self.cleanup_expired()

    def _schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS groups (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                icon       TEXT NOT NULL DEFAULT 'folder',
                color      TEXT NOT NULL DEFAULT '#FFFFFF',
                created_at INTEGER DEFAULT (unixepoch())
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL DEFAULT 'New chat',
                group_id   TEXT REFERENCES groups(id) ON DELETE SET NULL,
                pinned     INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER DEFAULT (unixepoch()),
                updated_at INTEGER DEFAULT (unixepoch())
            );
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                neuron          TEXT,
                model           TEXT,
                sources         TEXT,
                created_at      INTEGER DEFAULT (unixepoch())
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
            CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);
        """)
        self._conn.commit()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        cutoff = int(time.time()) - _RETENTION_SEC
        rows = self._conn.execute(
            "SELECT id FROM conversations WHERE group_id IS NULL AND pinned = 0 AND updated_at < ?",
            (cutoff,)
        ).fetchall()
        for r in rows:
            self.delete_conversation(r["id"])
        return len(rows)

    # ── Groups ────────────────────────────────────────────────────────────────

    def list_groups(self) -> list[dict]:
        return [_row(r) for r in self._conn.execute(
            "SELECT * FROM groups ORDER BY created_at ASC"
        ).fetchall()]

    def create_group(self, name: str, icon: str = "folder", color: str = "#FFFFFF") -> dict:
        gid = _uid()
        self._conn.execute(
            "INSERT INTO groups (id, name, icon, color) VALUES (?, ?, ?, ?)",
            (gid, name.strip() or "Group", icon or "folder", color or "#FFFFFF"),
        )
        self._conn.commit()
        return _row(self._conn.execute("SELECT * FROM groups WHERE id = ?", (gid,)).fetchone())

    def rename_group(self, gid: str, name: str) -> None:
        self._conn.execute("UPDATE groups SET name = ? WHERE id = ?", (name.strip() or "Group", gid))
        self._conn.commit()

    def delete_group(self, gid: str) -> None:
        self._conn.execute("UPDATE conversations SET group_id = NULL WHERE group_id = ?", (gid,))
        self._conn.execute("DELETE FROM groups WHERE id = ?", (gid,))
        self._conn.commit()

    # ── Conversations ─────────────────────────────────────────────────────────

    def list_conversations(self) -> list[dict]:
        return [_row(r) for r in self._conn.execute(
            "SELECT * FROM conversations ORDER BY pinned DESC, updated_at DESC"
        ).fetchall()]

    def create_conversation(self, title: str = "New chat", group_id: Optional[str] = None) -> dict:
        cid = _uid()
        self._conn.execute(
            "INSERT INTO conversations (id, title, group_id) VALUES (?, ?, ?)",
            (cid, title, group_id)
        )
        self._conn.commit()
        return self.get_conversation(cid)  # type: ignore[return-value]

    def get_conversation(self, cid: str) -> Optional[dict]:
        row = self._conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
        return _row(row) if row else None

    def update_conversation(self, cid: str, patch: dict) -> None:
        cur = self.get_conversation(cid)
        if not cur:
            return
        self._conn.execute(
            "UPDATE conversations SET title = ?, group_id = ?, pinned = ?, updated_at = unixepoch() WHERE id = ?",
            (
                patch.get("title", cur["title"]),
                patch["group_id"] if "group_id" in patch else cur["group_id"],
                patch.get("pinned", cur["pinned"]),
                cid,
            )
        )
        self._conn.commit()

    def touch(self, cid: str) -> None:
        self._conn.execute("UPDATE conversations SET updated_at = unixepoch() WHERE id = ?", (cid,))
        self._conn.commit()

    def delete_conversation(self, cid: str) -> None:
        self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
        self._conn.commit()

    # ── Messages ──────────────────────────────────────────────────────────────

    def list_messages(self, cid: str) -> list[dict]:
        return [_row(r) for r in self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (cid,)
        ).fetchall()]

    def add_message(
        self, *,
        conversation_id: str,
        role: str,
        content: str,
        neuron: Optional[str] = None,
        model: Optional[str] = None,
        sources: Optional[str] = None,
    ) -> dict:
        cur = self._conn.execute(
            "INSERT INTO messages (conversation_id, role, content, neuron, model, sources) VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, role, content, neuron, model, sources)
        )
        self._conn.commit()
        self.touch(conversation_id)
        return _row(self._conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone())

    def history_for(self, cid: str, limit: int = 24) -> list[dict]:
        rows = self._conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (cid, limit)
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ── Singleton ─────────────────────────────────────────────────────────────────

_store: Optional[ChatStore] = None


def chat_store() -> ChatStore:
    global _store
    if _store is None:
        _store = ChatStore()
    return _store


def title_from_message(text: str) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= 48 else clean[:46] + "…"
