"""
SQLite 데이터베이스 설정. users, usage_logs, analysis_cache 테이블.
"""
import sqlite3
import logging
from pathlib import Path
from typing import Optional

from backend.config import ROOT_DIR

logger = logging.getLogger(__name__)

DB_PATH = ROOT_DIR / "data" / "policy.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """테이블 생성 (최초 1회 또는 마이그레이션 시)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            role TEXT NOT NULL DEFAULT 'USER',
            email_verified INTEGER NOT NULL DEFAULT 0,
            verification_token TEXT,
            verification_expires_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_login_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

        CREATE TABLE IF NOT EXISTS approval_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            note TEXT,
            decided_by INTEGER REFERENCES users(id),
            decided_at TEXT,
            decision_note TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ip TEXT,
            endpoint TEXT,
            action TEXT,
            request_id TEXT,
            input_chars INTEGER,
            output_chars INTEGER,
            model TEXT,
            token_in INTEGER,
            token_out INTEGER,
            cost_estimate REAL,
            status_code INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            latency_ms INTEGER,
            error_message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_usage_user_created ON usage_logs(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_logs(created_at);

        CREATE TABLE IF NOT EXISTS analysis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            cache_key TEXT UNIQUE NOT NULL,
            request_fingerprint TEXT,
            result_payload TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cache_key ON analysis_cache(cache_key);
        CREATE INDEX IF NOT EXISTS idx_cache_expires ON analysis_cache(expires_at);

        CREATE TABLE IF NOT EXISTS analysis_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,                -- check | verify
            input_text TEXT NOT NULL,
            options_json TEXT,                 -- verify 옵션(phase/top_k 등)
            result_text TEXT NOT NULL,         -- text 또는 json string
            result_format TEXT NOT NULL,       -- text | json
            status_code INTEGER NOT NULL,
            from_cache INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_hist_user_created ON analysis_history(user_id, created_at);
        """)
        for stmt in [
            "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN verification_token TEXT",
            "ALTER TABLE users ADD COLUMN verification_expires_at TEXT",
            "ALTER TABLE users ADD COLUMN name TEXT",
            "ALTER TABLE users ADD COLUMN phone TEXT",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        conn.commit()
        logger.info("DB 초기화 완료: %s", DB_PATH)
    finally:
        conn.close()
