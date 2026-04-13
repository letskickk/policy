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
        CREATE INDEX IF NOT EXISTS idx_usage_action ON usage_logs(action, created_at);

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

        CREATE TABLE IF NOT EXISTS pledge_chat_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            topic TEXT NOT NULL,
            output_format TEXT NOT NULL DEFAULT '정책',
            status TEXT NOT NULL DEFAULT 'active',
            final_draft TEXT,
            rag_context TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON pledge_chat_sessions(user_id, created_at);

        CREATE TABLE IF NOT EXISTS pledge_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES pledge_chat_sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON pledge_chat_messages(session_id, created_at);

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
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            share_summary_title TEXT,
            share_summary_headline TEXT,
            share_summary_bullets TEXT,
            share_summary_version TEXT,
            share_summary_source_hash TEXT,
            share_summary_updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_pledges_candidate ON candidate_pledges(candidate_id);
        CREATE INDEX IF NOT EXISTS idx_candidate_pledges_candidate_priority_created
            ON candidate_pledges(candidate_id, priority, created_at);

        CREATE TABLE IF NOT EXISTS candidate_pledge_review_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
            snapshot_group TEXT NOT NULL,
            source_action TEXT NOT NULL DEFAULT 'REVIEW',
            approval_status TEXT NOT NULL DEFAULT 'PENDING',
            rejection_reason TEXT,
            reviewed_at TEXT NOT NULL DEFAULT (datetime('now')),
            pledge_id INTEGER,
            title TEXT NOT NULL,
            content TEXT,
            priority INTEGER NOT NULL DEFAULT 100,
            total_score REAL,
            analysis_result TEXT,
            analyzed_at TEXT,
            pledge_created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_pledge_review_history_candidate
            ON candidate_pledge_review_history(candidate_id, reviewed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_candidate_pledge_review_history_group
            ON candidate_pledge_review_history(snapshot_group);

        CREATE TABLE IF NOT EXISTS candidate_external_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
            source_key TEXT NOT NULL,
            external_id TEXT,
            external_profile_url TEXT,
            external_photo_url TEXT,
            external_support_url TEXT,
            external_bio TEXT,
            matched_name TEXT,
            matched_region TEXT,
            matched_district TEXT,
            matched_position TEXT,
            raw_payload_json TEXT NOT NULL DEFAULT '{}',
            last_synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(candidate_id, source_key)
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_external_profiles_candidate
            ON candidate_external_profiles(candidate_id);
        CREATE INDEX IF NOT EXISTS idx_candidate_external_profiles_source_external
            ON candidate_external_profiles(source_key, external_id);

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

        CREATE TABLE IF NOT EXISTS policy_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            summary TEXT,
            official_summary TEXT,
            key_points TEXT,
            relevance_note TEXT,
            body TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            owner_scope TEXT NOT NULL DEFAULT 'party',
            effective_from TEXT,
            effective_to TEXT,
            version_label TEXT,
            created_by INTEGER REFERENCES users(id),
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_policy_positions_status ON policy_positions(status);
        CREATE INDEX IF NOT EXISTS idx_policy_positions_category ON policy_positions(category);

        CREATE TABLE IF NOT EXISTS policy_position_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL REFERENCES policy_positions(id) ON DELETE CASCADE,
            version_label TEXT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            summary TEXT,
            official_summary TEXT,
            key_points TEXT,
            relevance_note TEXT,
            body TEXT,
            status TEXT NOT NULL,
            owner_scope TEXT NOT NULL,
            effective_from TEXT,
            effective_to TEXT,
            snapshot_type TEXT NOT NULL DEFAULT 'update',
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_policy_position_versions_position
            ON policy_position_versions(position_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS policy_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            doc_type TEXT NOT NULL,
            summary TEXT,
            body TEXT,
            speaker TEXT,
            speaker_name TEXT,
            owner_name TEXT,
            source_url TEXT,
            source_ref TEXT,
            published_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_by INTEGER REFERENCES users(id),
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_policy_documents_type ON policy_documents(doc_type);
        CREATE INDEX IF NOT EXISTS idx_policy_documents_status ON policy_documents(status);
        CREATE INDEX IF NOT EXISTS idx_policy_documents_published_at ON policy_documents(published_at);

        CREATE TABLE IF NOT EXISTS policy_link_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES policy_documents(id) ON DELETE CASCADE,
            position_id INTEGER NOT NULL REFERENCES policy_positions(id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL DEFAULT 'explains',
            score REAL NOT NULL DEFAULT 0,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(document_id, position_id)
        );
        CREATE INDEX IF NOT EXISTS idx_policy_link_suggestions_document
            ON policy_link_suggestions(document_id, status, score DESC);
        CREATE INDEX IF NOT EXISTS idx_policy_link_suggestions_position
            ON policy_link_suggestions(position_id, status, score DESC);

        CREATE TABLE IF NOT EXISTS policy_document_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL REFERENCES policy_positions(id) ON DELETE CASCADE,
            document_id INTEGER NOT NULL REFERENCES policy_documents(id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL DEFAULT 'references',
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(position_id, document_id, relation_type)
        );
        CREATE INDEX IF NOT EXISTS idx_policy_document_links_position ON policy_document_links(position_id);
        CREATE INDEX IF NOT EXISTS idx_policy_document_links_document ON policy_document_links(document_id);

        CREATE TABLE IF NOT EXISTS policy_document_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES policy_documents(id) ON DELETE CASCADE,
            person_name TEXT NOT NULL,
            person_role TEXT NOT NULL,
            party_affiliation TEXT,
            is_reform_party INTEGER NOT NULL DEFAULT 0,
            is_primary INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(document_id, person_name, person_role)
        );
        CREATE INDEX IF NOT EXISTS idx_policy_document_people_document
            ON policy_document_people(document_id, person_role, is_primary DESC, person_name ASC);
        CREATE INDEX IF NOT EXISTS idx_policy_document_people_party
            ON policy_document_people(is_reform_party, person_name ASC);

        CREATE TABLE IF NOT EXISTS policy_ingest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            imported_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_policy_ingest_runs_source_started
            ON policy_ingest_runs(source_key, started_at DESC);

        CREATE TABLE IF NOT EXISTS policy_featured_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL REFERENCES policy_positions(id) ON DELETE CASCADE,
            reason TEXT,
            priority_score REAL NOT NULL DEFAULT 0,
            manual_weight INTEGER NOT NULL DEFAULT 0,
            start_at TEXT,
            end_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_by INTEGER REFERENCES users(id),
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_policy_featured_issues_status_dates
            ON policy_featured_issues(status, start_at DESC, end_at DESC);
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
            "ALTER TABLE candidate_pledges ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'PENDING'",
            "ALTER TABLE candidate_pledges ADD COLUMN rejection_reason TEXT",
            "ALTER TABLE candidate_pledges ADD COLUMN share_summary_title TEXT",
            "ALTER TABLE candidate_pledges ADD COLUMN share_summary_headline TEXT",
            "ALTER TABLE candidate_pledges ADD COLUMN share_summary_bullets TEXT",
            "ALTER TABLE candidate_pledges ADD COLUMN share_summary_version TEXT",
            "ALTER TABLE candidate_pledges ADD COLUMN share_summary_source_hash TEXT",
            "ALTER TABLE candidate_pledges ADD COLUMN share_summary_updated_at TEXT",
            "ALTER TABLE analysis_history ADD COLUMN total_score REAL",
            "ALTER TABLE candidates ADD COLUMN rejection_reason TEXT",
            "ALTER TABLE policy_documents ADD COLUMN speaker_name TEXT",
            "ALTER TABLE policy_positions ADD COLUMN official_summary TEXT",
            "ALTER TABLE policy_positions ADD COLUMN key_points TEXT",
            "ALTER TABLE policy_positions ADD COLUMN relevance_note TEXT",
            "ALTER TABLE policy_position_versions ADD COLUMN official_summary TEXT",
            "ALTER TABLE policy_position_versions ADD COLUMN key_points TEXT",
            "ALTER TABLE policy_position_versions ADD COLUMN relevance_note TEXT",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_score ON candidate_pledges(total_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_approval ON candidate_pledges(candidate_id, approval_status)")
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

        # ── 지원서(공천 신청) 검증용 ──
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS party_applicants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            region_province TEXT,
            district_info TEXT,
            election_position TEXT,
            doc_submitted INTEGER NOT NULL DEFAULT 0,
            interview_done INTEGER NOT NULL DEFAULT 0,
            status_note TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_pa_phone ON party_applicants(phone);
        CREATE INDEX IF NOT EXISTS idx_pa_email ON party_applicants(email);
        CREATE INDEX IF NOT EXISTS idx_pa_name ON party_applicants(name);
        """)
        for stmt in [
            "ALTER TABLE users ADD COLUMN applicant_verified INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN applicant_match_id INTEGER",
            "ALTER TABLE users ADD COLUMN applicant_match_note TEXT",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

        # ── 이슈 레이더 캐시 ──
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS issue_radar_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_json TEXT NOT NULL,
            scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS assembly_data_cache (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            data_json TEXT NOT NULL,
            cached_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS policy_review_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL REFERENCES policy_positions(id) ON DELETE CASCADE,
            author_id INTEGER REFERENCES users(id),
            comment TEXT NOT NULL,
            comment_type TEXT DEFAULT 'review',
            resolved INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_review_comments_position
            ON policy_review_comments(position_id, created_at);
        """)

        # ── 시민 제안 (Phase 6) ──
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS citizen_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_name TEXT,
            author_email TEXT,
            topic TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            cluster_id INTEGER,
            status TEXT DEFAULT 'new',
            reviewed_by INTEGER REFERENCES users(id),
            review_note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_citizen_proposals_status
            ON citizen_proposals(status, created_at);
        """)

        # ── Hub FTS5 전문 검색 인덱스 ──
        conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS hub_docs_fts USING fts5(
            title, summary, body,
            content=policy_documents, content_rowid=id,
            tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS hub_positions_fts USING fts5(
            title, summary, body,
            content=policy_positions, content_rowid=id,
            tokenize='unicode61'
        );
        """)

        # FTS 동기화 트리거 (policy_documents)
        for op, trig_body in [
            ("INSERT", "INSERT INTO hub_docs_fts(rowid, title, summary, body) VALUES (new.id, new.title, new.summary, new.body)"),
            ("DELETE", "INSERT INTO hub_docs_fts(hub_docs_fts, rowid, title, summary, body) VALUES('delete', old.id, old.title, old.summary, old.body)"),
            ("UPDATE", "INSERT INTO hub_docs_fts(hub_docs_fts, rowid, title, summary, body) VALUES('delete', old.id, old.title, old.summary, old.body); INSERT INTO hub_docs_fts(rowid, title, summary, body) VALUES (new.id, new.title, new.summary, new.body)"),
        ]:
            try:
                conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS hub_docs_fts_ai_{op.lower()}
                    AFTER {op} ON policy_documents BEGIN {trig_body}; END
                """)
            except sqlite3.OperationalError:
                pass

        # FTS 동기화 트리거 (policy_positions)
        for op, trig_body in [
            ("INSERT", "INSERT INTO hub_positions_fts(rowid, title, summary, body) VALUES (new.id, new.title, new.summary, new.body)"),
            ("DELETE", "INSERT INTO hub_positions_fts(hub_positions_fts, rowid, title, summary, body) VALUES('delete', old.id, old.title, old.summary, old.body)"),
            ("UPDATE", "INSERT INTO hub_positions_fts(hub_positions_fts, rowid, title, summary, body) VALUES('delete', old.id, old.title, old.summary, old.body); INSERT INTO hub_positions_fts(rowid, title, summary, body) VALUES (new.id, new.title, new.summary, new.body)"),
        ]:
            try:
                conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS hub_positions_fts_ai_{op.lower()}
                    AFTER {op} ON policy_positions BEGIN {trig_body}; END
                """)
            except sqlite3.OperationalError:
                pass

        conn.commit()
        logger.info("DB 초기화 완료: %s", DB_PATH)
    finally:
        conn.close()


def rebuild_hub_fts() -> None:
    """기존 데이터로 FTS 인덱스 재구축 (백필)."""
    conn = get_connection()
    try:
        conn.execute("INSERT INTO hub_docs_fts(hub_docs_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO hub_positions_fts(hub_positions_fts) VALUES('rebuild')")
        conn.commit()
        logger.info("Hub FTS 인덱스 재구축 완료")
    finally:
        conn.close()
