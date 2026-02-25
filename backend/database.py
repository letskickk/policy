"""
SQLite 데이터베이스 설정. users, usage_logs, analysis_cache 테이블.
"""
import sqlite3
import logging
from pathlib import Path

from backend.config import ROOT_DIR, DATABASE_PATH

logger = logging.getLogger(__name__)

DB_PATH = DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
            kind TEXT NOT NULL,
            input_text TEXT NOT NULL,
            options_json TEXT,
            result_text TEXT NOT NULL,
            result_format TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            from_cache INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_hist_user_created ON analysis_history(user_id, created_at);

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            district_name TEXT,
            region_code TEXT NOT NULL,
            election_type TEXT NOT NULL DEFAULT 'local',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_candidates_region_code ON candidates(region_code);
        CREATE INDEX IF NOT EXISTS idx_candidates_region_election ON candidates(region_code, election_type);

        CREATE TABLE IF NOT EXISTS candidate_pledges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            category TEXT,
            priority INTEGER NOT NULL DEFAULT 100,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_pledges_candidate ON candidate_pledges(candidate_id);
        CREATE INDEX IF NOT EXISTS idx_candidate_pledges_candidate_priority_created
            ON candidate_pledges(candidate_id, priority, created_at);

        CREATE TABLE IF NOT EXISTS region_codes (
            region_code TEXT PRIMARY KEY,
            region_name TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS district_codes (
            district_code TEXT PRIMARY KEY,
            district_name TEXT NOT NULL,
            region_code TEXT NOT NULL REFERENCES region_codes(region_code),
            election_type TEXT NOT NULL DEFAULT 'local',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_district_codes_region ON district_codes(region_code);
        CREATE INDEX IF NOT EXISTS idx_district_codes_region_election ON district_codes(region_code, election_type);

        CREATE TABLE IF NOT EXISTS winners2022 (
            huboid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sg_typecode TEXT,
            sd_name TEXT,
            sgg_name TEXT,
            wiw_name TEXT,
            position TEXT,
            region TEXT,
            fetched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_winners2022_typecode ON winners2022(sg_typecode);
        CREATE INDEX IF NOT EXISTS idx_winners2022_sd_name ON winners2022(sd_name);

        CREATE TABLE IF NOT EXISTS winner_pledges2022 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            huboid TEXT NOT NULL REFERENCES winners2022(huboid),
            title TEXT,
            content TEXT,
            realm TEXT,
            fetched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_winner_pledges2022_huboid ON winner_pledges2022(huboid);
        """)
        for stmt in [
            "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN verification_token TEXT",
            "ALTER TABLE users ADD COLUMN verification_expires_at TEXT",
            "ALTER TABLE users ADD COLUMN name TEXT",
            "ALTER TABLE users ADD COLUMN phone TEXT",
            "ALTER TABLE users ADD COLUMN election_position TEXT",
            "ALTER TABLE users ADD COLUMN region_code TEXT",
            "ALTER TABLE users ADD COLUMN region_name TEXT",
            "ALTER TABLE users ADD COLUMN district_code TEXT",
            "ALTER TABLE users ADD COLUMN district_name TEXT",
            "ALTER TABLE candidates ADD COLUMN district_code TEXT",
            "ALTER TABLE candidates ADD COLUMN election_level TEXT NOT NULL DEFAULT 'regional'",
            "ALTER TABLE candidates ADD COLUMN user_id INTEGER REFERENCES users(id)",
            "ALTER TABLE candidates ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'PENDING'",
            "ALTER TABLE candidate_pledges ADD COLUMN content TEXT",
            "ALTER TABLE candidate_pledges ADD COLUMN total_score REAL",
            "ALTER TABLE candidate_pledges ADD COLUMN analysis_result TEXT",
            "ALTER TABLE candidate_pledges ADD COLUMN analyzed_at TEXT",
            "ALTER TABLE analysis_history ADD COLUMN total_score REAL",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_score ON candidate_pledges(total_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_score ON analysis_history(total_score, created_at)")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS weekly_champions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            candidate_id INTEGER NOT NULL,
            candidate_name TEXT NOT NULL,
            region_name TEXT,
            district_name TEXT,
            election_type TEXT,
            avg_score REAL NOT NULL,
            scored_pledge_count INTEGER NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(week_start)
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_district_code ON candidates(district_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_region_district ON candidates(region_code, district_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_user_id ON candidates(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_approval ON candidates(approval_status)")
        conn.commit()
        logger.info("DB 초기화 완료: %s", DB_PATH)
    finally:
        conn.close()
