"""
database/db.py
SQLite DB 연결 및 테이블 관리
"""

import os
import aiosqlite
from pathlib import Path
from datetime import datetime, timezone, timedelta

DB_PATH = Path("database/voices.db")

# 한국 시간대 (UTC+9)
KST = timezone(timedelta(hours=9))


def now_kst() -> str:
    """현재 한국 시간을 문자열로 반환"""
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


async def init_db():
    """DB 초기화 - 테이블 없으면 자동 생성"""
    DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS voices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                duration REAL,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tts_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                voice_id INTEGER,
                input_text TEXT NOT NULL,
                audio_path TEXT,
                srt_path TEXT,
                created_at TEXT,
                FOREIGN KEY (voice_id) REFERENCES voices(id)
            )
        """)
        await db.commit()

        # 프로젝트 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                voice_id INTEGER,
                input_text TEXT,
                session_id TEXT,
                lines_json TEXT,
                global_tone TEXT DEFAULT 'normal',
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (voice_id) REFERENCES voices(id)
            )
        """)
        await db.commit()

        # 마이그레이션: favorites 컬럼 추가
        try:
            await db.execute("ALTER TABLE voices ADD COLUMN is_favorite INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass  # 이미 존재

        # 음성 파라미터 체크포인트 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS voice_param_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                voice_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                params_json TEXT NOT NULL,
                avg_score REAL DEFAULT 0,
                feedback_count INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 0,
                created_at TEXT,
                FOREIGN KEY (voice_id) REFERENCES voices(id)
            )
        """)

        # 피드백 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS param_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checkpoint_id INTEGER,
                voice_id INTEGER NOT NULL,
                params_json TEXT NOT NULL,
                score INTEGER NOT NULL,
                preview_text TEXT,
                created_at TEXT,
                FOREIGN KEY (checkpoint_id) REFERENCES voice_param_checkpoints(id),
                FOREIGN KEY (voice_id) REFERENCES voices(id)
            )
        """)
        await db.commit()

        # 사용자 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                display_name TEXT NOT NULL,
                level INTEGER DEFAULT 255,
                auth_provider TEXT DEFAULT 'email',
                google_id TEXT,
                email_verified INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # 이메일 인증 토큰 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS email_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.commit()

        # 마이그레이션: plan, user_type 컬럼 추가
        for col, default in [("plan", "free"), ("user_type", "general")]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT '{default}'")
                await db.commit()
                # 기존 관리자(level<=10)는 max 플랜으로
                await db.execute("UPDATE users SET plan = 'max' WHERE level <= 10 AND plan = 'free'")
                await db.commit()
                print(f"[OK] users 테이블에 {col} 컬럼 추가")
            except Exception:
                pass

        # 기존 테이블에 user_id 컬럼 추가 (마이그레이션)
        for table in ["voices", "tts_results", "projects", "voice_param_checkpoints"]:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER DEFAULT NULL")
                await db.commit()
            except Exception:
                pass

        # voices 테이블 마이그레이션: 폴더/배포 관련 컬럼
        voice_migrations = [
            ("folder_type", "TEXT DEFAULT 'personal'"),      # personal, shared, published
            ("status", "TEXT DEFAULT 'active'"),              # active, review, approved, published
            ("source_voice_id", "INTEGER DEFAULT NULL"),      # 복사 원본 ID
            ("shared_folder_id", "INTEGER DEFAULT NULL"),     # 공유 폴더 ID
            ("can_generate", "INTEGER DEFAULT 0"),            # 음성 생성 권한 (관리자 부여)
            ("publish_version", "INTEGER DEFAULT 1"),         # 배포 버전
            ("description", "TEXT DEFAULT ''"),               # 음원 설명
        ]
        for col, col_type in voice_migrations:
            try:
                await db.execute(f"ALTER TABLE voices ADD COLUMN {col} {col_type}")
                await db.commit()
                print(f"[OK] voices 테이블에 {col} 컬럼 추가")
            except Exception:
                pass

        # 공유 폴더 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shared_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                owner_id INTEGER NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (owner_id) REFERENCES users(id)
            )
        """)

        # 공유 폴더 멤버 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shared_folder_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                permission TEXT DEFAULT 'view',
                created_at TEXT,
                FOREIGN KEY (folder_id) REFERENCES shared_folders(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(folder_id, user_id)
            )
        """)

        # 배포 심사 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS voice_publish_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                voice_id INTEGER NOT NULL,
                shared_folder_id INTEGER,
                requested_by INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                reviewed_by INTEGER,
                review_note TEXT DEFAULT '',
                created_at TEXT,
                reviewed_at TEXT,
                FOREIGN KEY (voice_id) REFERENCES voices(id),
                FOREIGN KEY (requested_by) REFERENCES users(id),
                FOREIGN KEY (reviewed_by) REFERENCES users(id)
            )
        """)
        await db.commit()

        # ── 중국어 발음 학습 테이블 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chinese_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chinese TEXT NOT NULL,
                pinyin TEXT NOT NULL,
                tones TEXT,
                meaning_ko TEXT,
                category TEXT DEFAULT 'general',
                ref_audio_path TEXT,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS pronunciation_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                user_id INTEGER,
                audio_path TEXT NOT NULL,
                stt_text TEXT,
                score INTEGER DEFAULT 0,
                tone_score INTEGER DEFAULT 0,
                tone_detail TEXT,
                feedback TEXT,
                is_starred INTEGER DEFAULT 0,
                created_at TEXT,
                FOREIGN KEY (card_id) REFERENCES chinese_cards(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.commit()

        # 중국어 메모 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chinese_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                card_id INTEGER,
                subject TEXT DEFAULT '',
                week INTEGER DEFAULT 0,
                content TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (card_id) REFERENCES chinese_cards(id)
            )
        """)
        await db.commit()

        # 사용자 설정 테이블
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                settings_json TEXT DEFAULT '{}',
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.commit()

        # 중국어 카드 마이그레이션
        for col, col_type in [("subject", "TEXT DEFAULT ''"), ("week", "INTEGER DEFAULT 0"),
                              ("class_num", "INTEGER DEFAULT 0"),
                              ("group_name", "TEXT DEFAULT ''"),
                              ("sort_order", "INTEGER DEFAULT 0"),
                              ("pinyin_sandhi", "TEXT DEFAULT ''"),
                              ("sandhi_notes", "TEXT DEFAULT ''"),
                              ("meaning_en", "TEXT DEFAULT ''")]:
            try:
                await db.execute(f"ALTER TABLE chinese_cards ADD COLUMN {col} {col_type}")
                await db.commit()
                print(f"[OK] chinese_cards 테이블에 {col} 컬럼 추가")
            except Exception:
                pass

        # 슈퍼 관리자 자동 생성 (레벨 0)
        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE level = 0")
        count = (await cursor.fetchone())[0]
        if count == 0:
            admin_email = os.environ.get("SUPER_ADMIN_EMAIL")
            admin_pw = os.environ.get("SUPER_ADMIN_PASSWORD")
            if admin_email and admin_pw:
                from auth.security import hash_password
                hashed = hash_password(admin_pw)
                await db.execute(
                    """INSERT INTO users (email, password_hash, display_name, level, email_verified, auth_provider, created_at, updated_at)
                       VALUES (?, ?, ?, 0, 1, 'email', ?, ?)""",
                    (admin_email, hashed, "Super Admin", now_kst(), now_kst())
                )
                await db.commit()
                print(f"[OK] 슈퍼 관리자 생성: {admin_email}")

                # 기존 데이터를 슈퍼 관리자에게 할당
                cursor = await db.execute("SELECT id FROM users WHERE level = 0")
                admin = await cursor.fetchone()
                if admin:
                    admin_id = admin[0]
                    for table in ["voices", "tts_results", "projects", "voice_param_checkpoints"]:
                        await db.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (admin_id,))
                    await db.commit()
                    print(f"[OK] 기존 데이터를 관리자(ID:{admin_id})에게 할당")

    print("[OK] DB init done")


async def save_voice(name: str, file_path: str, duration: float, user_id: int = None) -> int:
    """목소리 샘플 DB 저장 -> 저장된 ID 반환"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO voices (name, file_path, duration, created_at, user_id) VALUES (?, ?, ?, ?, ?)",
            (name, file_path, duration, now_kst(), user_id)
        )
        await db.commit()
        return cursor.lastrowid


async def get_all_voices(user_id: int = None) -> list:
    """저장된 목소리 목록 조회 (user_id 지정 시 해당 사용자만)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if user_id is not None:
            cursor = await db.execute("SELECT * FROM voices WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
        else:
            cursor = await db.execute("SELECT * FROM voices ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_voice(voice_id: int) -> dict:
    """목소리 단건 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM voices WHERE id = ?", (voice_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_voice(voice_id: int):
    """목소리 DB에서 삭제"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM voices WHERE id = ?", (voice_id,))
        await db.commit()


async def delete_all_voices():
    """목소리 전체 삭제"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM voices")
        await db.commit()


async def save_tts_result(
    voice_id: int,
    input_text: str,
    audio_path: str,
    srt_path: str
) -> int:
    """TTS 생성 결과 DB 저장 -> 저장된 ID 반환"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO tts_results
               (voice_id, input_text, audio_path, srt_path, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (voice_id, input_text, audio_path, srt_path, now_kst())
        )
        await db.commit()
        return cursor.lastrowid


async def get_tts_result(result_id: int) -> dict:
    """TTS 결과 단건 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tts_results WHERE id = ?", (result_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_tts_results() -> list:
    """TTS 결과 전체 조회 (목소리 이름 포함)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT t.*, v.name as voice_name
               FROM tts_results t
               LEFT JOIN voices v ON t.voice_id = v.id
               ORDER BY t.created_at DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def delete_tts_result(result_id: int):
    """TTS 결과 DB에서 삭제"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tts_results WHERE id = ?", (result_id,))
        await db.commit()


async def delete_all_tts_results():
    """TTS 결과 전체 삭제"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tts_results")
        await db.commit()


async def search_tts_results(page: int = 1, per_page: int = 20, search: str = "", voice_name: str = "") -> dict:
    """TTS 결과 검색 (페이지네이션 + 필터링)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        where_clauses = []
        params = []

        if search:
            where_clauses.append("t.input_text LIKE ?")
            params.append(f"%{search}%")
        if voice_name:
            where_clauses.append("v.name = ?")
            params.append(voice_name)

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        # 총 개수
        count_sql = f"SELECT COUNT(*) FROM tts_results t LEFT JOIN voices v ON t.voice_id = v.id {where_sql}"
        cursor = await db.execute(count_sql, params)
        total = (await cursor.fetchone())[0]

        # 결과
        offset = (page - 1) * per_page
        data_sql = f"""SELECT t.*, v.name as voice_name
                       FROM tts_results t
                       LEFT JOIN voices v ON t.voice_id = v.id
                       {where_sql}
                       ORDER BY t.created_at DESC
                       LIMIT ? OFFSET ?"""
        cursor = await db.execute(data_sql, params + [per_page, offset])
        rows = await cursor.fetchall()

        total_pages = max(1, (total + per_page - 1) // per_page)

        return {
            "results": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }


async def toggle_favorite(voice_id: int) -> bool:
    """즐겨찾기 토글 -> 새로운 상태 반환"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT is_favorite FROM voices WHERE id = ?", (voice_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        new_val = 0 if row[0] else 1
        await db.execute("UPDATE voices SET is_favorite = ? WHERE id = ?", (new_val, voice_id))
        await db.commit()
        return bool(new_val)


async def get_voice_names() -> list:
    """고유한 목소리 이름 목록 (검색 필터용)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT name FROM voices ORDER BY name")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


# ── 프로젝트 CRUD ──

async def save_project(name: str, voice_id: int, input_text: str,
                        session_id: str, lines_json: str, global_tone: str = "normal") -> int:
    """프로젝트 저장 -> ID 반환"""
    now = now_kst()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO projects (name, voice_id, input_text, session_id, lines_json, global_tone, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, voice_id, input_text, session_id, lines_json, global_tone, now, now)
        )
        await db.commit()
        return cursor.lastrowid


async def update_project(project_id: int, name: str, voice_id: int, input_text: str,
                          session_id: str, lines_json: str, global_tone: str = "normal"):
    """프로젝트 업데이트"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE projects SET name=?, voice_id=?, input_text=?, session_id=?,
               lines_json=?, global_tone=?, updated_at=? WHERE id=?""",
            (name, voice_id, input_text, session_id, lines_json, global_tone, now_kst(), project_id)
        )
        await db.commit()


async def get_all_projects() -> list:
    """프로젝트 목록 조회 (음색 이름 포함)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT p.*, v.name as voice_name
               FROM projects p LEFT JOIN voices v ON p.voice_id = v.id
               ORDER BY p.updated_at DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_project(project_id: int) -> dict:
    """프로젝트 단건 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT p.*, v.name as voice_name
               FROM projects p LEFT JOIN voices v ON p.voice_id = v.id
               WHERE p.id = ?""", (project_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_project(project_id: int):
    """프로젝트 삭제"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await db.commit()


# ── 음성 파라미터 체크포인트 CRUD ──

async def save_checkpoint(voice_id: int, name: str, params_json: str) -> int:
    """체크포인트 저장 -> ID 반환"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO voice_param_checkpoints (voice_id, name, params_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (voice_id, name, params_json, now_kst())
        )
        await db.commit()
        return cursor.lastrowid


async def get_checkpoints(voice_id: int) -> list:
    """해당 음성의 체크포인트 목록 (점수순)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM voice_param_checkpoints
               WHERE voice_id = ? ORDER BY avg_score DESC, created_at DESC""",
            (voice_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_checkpoint(checkpoint_id: int) -> dict:
    """체크포인트 단건 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM voice_param_checkpoints WHERE id = ?", (checkpoint_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def set_active_checkpoint(voice_id: int, checkpoint_id: int):
    """체크포인트 활성화 (같은 음성의 다른 체크포인트는 비활성)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE voice_param_checkpoints SET is_active = 0 WHERE voice_id = ?",
            (voice_id,)
        )
        await db.execute(
            "UPDATE voice_param_checkpoints SET is_active = 1 WHERE id = ?",
            (checkpoint_id,)
        )
        await db.commit()


async def get_active_checkpoint(voice_id: int) -> dict:
    """활성 체크포인트 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM voice_param_checkpoints WHERE voice_id = ? AND is_active = 1",
            (voice_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_checkpoint(checkpoint_id: int):
    """체크포인트 삭제"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM param_feedback WHERE checkpoint_id = ?", (checkpoint_id,))
        await db.execute("DELETE FROM voice_param_checkpoints WHERE id = ?", (checkpoint_id,))
        await db.commit()


async def save_feedback(checkpoint_id: int, voice_id: int, params_json: str,
                        score: int, preview_text: str = ""):
    """피드백 저장 + 체크포인트 평균 점수 업데이트"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO param_feedback (checkpoint_id, voice_id, params_json, score, preview_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (checkpoint_id, voice_id, params_json, score, preview_text, now_kst())
        )
        # 체크포인트 평균 점수 업데이트
        if checkpoint_id:
            cursor = await db.execute(
                "SELECT AVG(score), COUNT(*) FROM param_feedback WHERE checkpoint_id = ?",
                (checkpoint_id,)
            )
            row = await cursor.fetchone()
            if row:
                await db.execute(
                    "UPDATE voice_param_checkpoints SET avg_score = ?, feedback_count = ? WHERE id = ?",
                    (row[0] or 0, row[1] or 0, checkpoint_id)
                )
        await db.commit()


# ── 중국어 발음 학습 CRUD ──

async def save_chinese_card(user_id: int, chinese: str, pinyin: str, tones: str,
                            meaning_ko: str, category: str = "general",
                            ref_audio_path: str = None,
                            subject: str = "", week: int = 0,
                            class_num: int = 0) -> int:
    """중국어 학습 카드 저장 -> ID 반환 (성조 변조 자동 계산)"""
    # 성조 변조 계산
    pinyin_sandhi = ''
    sandhi_notes = ''
    try:
        if chinese and any('\u4e00' <= ch <= '\u9fff' for ch in chinese):
            from pypinyin import lazy_pinyin, Style
            sandhi_list = lazy_pinyin(chinese, style=Style.TONE, tone_sandhi=True) or []
            orig_list = lazy_pinyin(chinese, style=Style.TONE, tone_sandhi=False) or []
            pinyin_sandhi = ' '.join(sandhi_list)
            notes = []
            for i, (o, s) in enumerate(zip(orig_list, sandhi_list)):
                if o != s and i < len(chinese):
                    notes.append(f"{chinese[i]}: {o}→{s}")
            sandhi_notes = ' | '.join(notes[:3])
    except Exception:
        pass

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO chinese_cards
               (user_id, chinese, pinyin, tones, meaning_ko, category, ref_audio_path, subject, week, class_num, pinyin_sandhi, sandhi_notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, chinese, pinyin, tones, meaning_ko, category, ref_audio_path, subject, week, class_num, pinyin_sandhi, sandhi_notes, now_kst())
        )
        await db.commit()
        return cursor.lastrowid


async def get_chinese_cards(user_id: int, category: str = None,
                            subject: str = None, week: int = None) -> list:
    """학습 카드 목록 조회 (최고점수/시도횟수 포함)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where = ["(c.user_id = ? OR c.category IN ('textbook','pronunciation'))"]
        params = [user_id]
        if category:
            where.append("c.category = ?")
            params.append(category)
        if subject:
            where.append("c.subject = ?")
            params.append(subject)
        if week is not None and week > 0:
            where.append("c.week = ?")
            params.append(week)
        sql = f"""SELECT c.*, MAX(p.score) as best_score, COUNT(p.id) as attempts,
                  SUM(CASE WHEN p.score >= 100 THEN 1 ELSE 0 END) as perfect_count
                  FROM chinese_cards c
                  LEFT JOIN pronunciation_records p ON c.id = p.card_id
                  WHERE {' AND '.join(where)}
                  GROUP BY c.id ORDER BY c.subject, c.week, c.class_num, c.id"""
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_chinese_subjects(user_id: int) -> list:
    """과목 + 주차 + 교시 목록 조회 (교재는 전체 공유)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT subject, week, class_num, COUNT(*) as card_count
               FROM chinese_cards WHERE (user_id = ? OR category IN ('textbook','pronunciation')) AND subject != ''
               GROUP BY subject, week, class_num ORDER BY subject, week, class_num""",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [{"subject": r[0], "week": r[1], "class_num": r[2], "card_count": r[3]} for r in rows]


async def save_chinese_note(user_id: int, content: str, card_id: int = None,
                            subject: str = "", week: int = 0) -> int:
    """메모 저장 -> ID 반환"""
    now = now_kst()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO chinese_notes (user_id, card_id, subject, week, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, card_id, subject, week, content, now, now)
        )
        await db.commit()
        return cursor.lastrowid


async def update_chinese_note(note_id: int, content: str):
    """메모 수정"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE chinese_notes SET content = ?, updated_at = ? WHERE id = ?",
            (content, now_kst(), note_id)
        )
        await db.commit()


async def get_chinese_notes(user_id: int, subject: str = None, week: int = None,
                            card_id: int = None) -> list:
    """메모 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where = ["user_id = ?"]
        params = [user_id]
        if card_id:
            where.append("card_id = ?")
            params.append(card_id)
        if subject:
            where.append("subject = ?")
            params.append(subject)
        if week is not None and week > 0:
            where.append("week = ?")
            params.append(week)
        sql = f"SELECT * FROM chinese_notes WHERE {' AND '.join(where)} ORDER BY updated_at DESC"
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def delete_chinese_note(note_id: int):
    """메모 삭제"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chinese_notes WHERE id = ?", (note_id,))
        await db.commit()


async def get_chinese_card(card_id: int) -> dict:
    """학습 카드 단건 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM chinese_cards WHERE id = ?", (card_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_chinese_card(card_id: int, **kwargs):
    """학습 카드 업데이트"""
    allowed = {"chinese", "pinyin", "tones", "meaning_ko", "category", "ref_audio_path"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [card_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE chinese_cards SET {set_clause} WHERE id = ?", values)
        await db.commit()


async def delete_chinese_card(card_id: int):
    """학습 카드 + 관련 녹음 전부 삭제"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pronunciation_records WHERE card_id = ?", (card_id,))
        await db.execute("DELETE FROM chinese_cards WHERE id = ?", (card_id,))
        await db.commit()


async def save_pronunciation_record(card_id: int, user_id: int, audio_path: str,
                                     stt_text: str = "", score: int = 0,
                                     tone_score: int = 0, tone_detail: str = "",
                                     feedback: str = "") -> int:
    """발음 녹음 기록 저장 -> ID 반환"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO pronunciation_records
               (card_id, user_id, audio_path, stt_text, score, tone_score, tone_detail, feedback, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (card_id, user_id, audio_path, stt_text, score, tone_score, tone_detail, feedback, now_kst())
        )
        await db.commit()
        return cursor.lastrowid


async def get_pronunciation_records(card_id: int, user_id: int = None) -> list:
    """특정 카드의 발음 녹음 이력 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if user_id:
            cursor = await db.execute(
                "SELECT * FROM pronunciation_records WHERE card_id = ? AND user_id = ? ORDER BY created_at DESC",
                (card_id, user_id)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM pronunciation_records WHERE card_id = ? ORDER BY created_at DESC",
                (card_id,)
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def toggle_pronunciation_star(record_id: int) -> bool:
    """발음 녹음 즐겨찾기(별표) 토글"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT is_starred FROM pronunciation_records WHERE id = ?", (record_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        new_val = 0 if row[0] else 1
        await db.execute("UPDATE pronunciation_records SET is_starred = ? WHERE id = ?", (new_val, record_id))
        await db.commit()
        return bool(new_val)


async def delete_pronunciation_record(record_id: int):
    """발음 녹음 기록 삭제"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pronunciation_records WHERE id = ?", (record_id,))
        await db.commit()


async def get_chinese_study_stats(user_id: int) -> dict:
    """학습 통계 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        # 전체 카드 수
        cursor = await db.execute("SELECT COUNT(*) FROM chinese_cards WHERE (user_id = ? OR category IN ('textbook','pronunciation'))", (user_id,))
        total_cards = (await cursor.fetchone())[0]

        # 전체 녹음 수
        cursor = await db.execute(
            "SELECT COUNT(*) FROM pronunciation_records WHERE user_id = ?", (user_id,))
        total_records = (await cursor.fetchone())[0]

        # 평균 점수
        cursor = await db.execute(
            "SELECT AVG(score) FROM pronunciation_records WHERE user_id = ? AND score > 0", (user_id,))
        row = await cursor.fetchone()
        avg_score = round(row[0], 1) if row[0] else 0

        # 별표 녹음 수
        cursor = await db.execute(
            "SELECT COUNT(*) FROM pronunciation_records WHERE user_id = ? AND is_starred = 1", (user_id,))
        starred_count = (await cursor.fetchone())[0]

        # 오늘 연습 수
        today = now_kst()[:10]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM pronunciation_records WHERE user_id = ? AND created_at LIKE ?",
            (user_id, f"{today}%"))
        today_count = (await cursor.fetchone())[0]

        return {
            "total_cards": total_cards,
            "total_records": total_records,
            "avg_score": avg_score,
            "starred_count": starred_count,
            "today_count": today_count,
        }


async def get_chinese_daily_stats(user_id: int, days: int = 30) -> list:
    """날짜별 학습 통계 (최근 N일)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT SUBSTR(created_at, 1, 10) as date,
                      COUNT(*) as count,
                      ROUND(AVG(score), 1) as avg_score,
                      MAX(score) as max_score
               FROM pronunciation_records
               WHERE user_id = ? AND created_at IS NOT NULL
               GROUP BY date ORDER BY date DESC LIMIT ?""",
            (user_id, days)
        )
        rows = await cursor.fetchall()
        return [{"date": r[0], "count": r[1], "avg_score": r[2] or 0, "max_score": r[3] or 0}
                for r in rows]


async def get_chinese_recent_records(user_id: int, limit: int = 20) -> list:
    """최근 연습 기록 (카드 정보 포함)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT p.*, c.chinese, c.pinyin, c.subject, c.week
               FROM pronunciation_records p
               LEFT JOIN chinese_cards c ON p.card_id = c.id
               WHERE p.user_id = ?
               ORDER BY p.created_at DESC LIMIT ?""",
            (user_id, limit)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ── 사용자 설정 ──

async def get_user_settings(user_id: int) -> dict:
    """사용자 설정 조회"""
    import json as _json
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT settings_json FROM user_settings WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row and row[0]:
            try:
                return _json.loads(row[0])
            except Exception:
                return {}
        return {}


async def save_user_settings(user_id: int, settings: dict):
    """사용자 설정 저장 (upsert)"""
    import json as _json
    settings_json = _json.dumps(settings, ensure_ascii=False)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO user_settings (user_id, settings_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET settings_json = ?, updated_at = ?""",
            (user_id, settings_json, now_kst(), settings_json, now_kst())
        )
        await db.commit()


# ── 사용자 CRUD ──

async def create_user(email: str, password_hash: str, display_name: str,
                      auth_provider: str = "email", google_id: str = None,
                      email_verified: int = 0, level: int = 200,
                      plan: str = "free", user_type: str = "general") -> int:
    """사용자 생성 -> ID 반환"""
    now = now_kst()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO users (email, password_hash, display_name, level, auth_provider, google_id, email_verified, is_active, plan, user_type, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (email, password_hash, display_name, level, auth_provider, google_id, email_verified, plan, user_type, now, now)
        )
        await db.commit()
        return cursor.lastrowid


async def get_user_by_email(email: str) -> dict:
    """이메일로 사용자 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE email = ?", (email,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_user_by_id(user_id: int) -> dict:
    """ID로 사용자 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_user_by_google_id(google_id: str) -> dict:
    """Google ID로 사용자 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE google_id = ?", (google_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_user(user_id: int, **kwargs):
    """사용자 정보 업데이트"""
    allowed = {"display_name", "level", "email_verified", "is_active", "password_hash", "google_id", "plan", "user_type"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now_kst()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        await db.commit()


async def get_all_users() -> list:
    """전체 사용자 목록 (관리자용)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, email, display_name, level, plan, user_type, auth_provider, email_verified, is_active, created_at FROM users ORDER BY id ASC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ── 이메일 인증 ──

async def save_verification_token(user_id: int, token: str, hours: int = 24):
    """인증 토큰 저장"""
    now = now_kst()
    expires = (datetime.now(KST) + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO email_verifications (user_id, token, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user_id, token, expires, now)
        )
        await db.commit()


async def verify_email_token(token: str) -> int:
    """인증 토큰 확인 -> user_id 반환 (실패 시 None)"""
    now = now_kst()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM email_verifications WHERE token = ? AND used = 0 AND expires_at > ?",
            (token, now)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        await db.execute("UPDATE email_verifications SET used = 1 WHERE id = ?", (row["id"],))
        await db.execute("UPDATE users SET email_verified = 1, updated_at = ? WHERE id = ?", (now, row["user_id"]))
        await db.commit()
        return row["user_id"]


# ── 음원 라이브러리 (폴더 시스템) ──

async def get_personal_voices(user_id: int) -> list:
    """개인 폴더 음원 목록 (본인 것, folder_type=personal)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT v.*, u.display_name as owner_name
               FROM voices v LEFT JOIN users u ON v.user_id = u.id
               WHERE v.user_id = ? AND (v.folder_type = 'personal' OR v.folder_type IS NULL)
               ORDER BY v.created_at DESC""",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_published_voices() -> list:
    """배포 폴더 음원 목록 (모든 회원 접근 가능, status=published)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT v.*, u.display_name as owner_name
               FROM voices v LEFT JOIN users u ON v.user_id = u.id
               WHERE v.folder_type = 'published'
               ORDER BY v.created_at DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def copy_voice_to_personal(voice_id: int, user_id: int) -> int:
    """음원을 개인 폴더로 복사 -> 새 voice ID 반환"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM voices WHERE id = ?", (voice_id,))
        original = await cursor.fetchone()
        if not original:
            return None

        import shutil
        import uuid
        original = dict(original)
        # 파일 복사
        src_path = Path(original["file_path"])
        if src_path.exists():
            new_filename = f"{uuid.uuid4().hex[:8]}_{src_path.name}"
            dst_path = Path("voice_samples") / new_filename
            shutil.copy2(str(src_path), str(dst_path))
            new_file_path = str(dst_path)
        else:
            new_file_path = original["file_path"]

        now = now_kst()
        cursor = await db.execute(
            """INSERT INTO voices (name, file_path, duration, created_at, user_id,
               folder_type, status, source_voice_id, description, can_generate)
               VALUES (?, ?, ?, ?, ?, 'personal', 'active', ?, ?, ?)""",
            (f"{original['name']} (복사)", new_file_path, original.get("duration", 0),
             now, user_id, voice_id, original.get("description", ""),
             original.get("can_generate", 0))
        )
        await db.commit()
        return cursor.lastrowid


async def copy_voice_to_shared(voice_id: int, folder_id: int, user_id: int) -> int:
    """개인 음원을 공유 폴더로 복사 -> 새 voice ID 반환"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM voices WHERE id = ?", (voice_id,))
        original = await cursor.fetchone()
        if not original:
            return None

        import shutil
        import uuid
        original = dict(original)
        src_path = Path(original["file_path"])
        if src_path.exists():
            new_filename = f"{uuid.uuid4().hex[:8]}_{src_path.name}"
            dst_path = Path("voice_samples") / new_filename
            shutil.copy2(str(src_path), str(dst_path))
            new_file_path = str(dst_path)
        else:
            new_file_path = original["file_path"]

        now = now_kst()
        cursor = await db.execute(
            """INSERT INTO voices (name, file_path, duration, created_at, user_id,
               folder_type, status, source_voice_id, shared_folder_id, description, can_generate)
               VALUES (?, ?, ?, ?, ?, 'shared', 'active', ?, ?, ?, ?)""",
            (original["name"], new_file_path, original.get("duration", 0),
             now, user_id, voice_id, folder_id, original.get("description", ""),
             original.get("can_generate", 0))
        )
        await db.commit()
        return cursor.lastrowid


async def update_voice_meta(voice_id: int, **kwargs):
    """음원 메타데이터 업데이트 (이름, 설명, can_generate 등)"""
    allowed = {"name", "description", "can_generate", "folder_type", "status",
               "shared_folder_id", "publish_version"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [voice_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE voices SET {set_clause} WHERE id = ?", values)
        await db.commit()


# ── 공유 폴더 CRUD ──

async def create_shared_folder(name: str, description: str, owner_id: int) -> int:
    """공유 폴더 생성 -> ID 반환"""
    now = now_kst()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO shared_folders (name, description, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, description, owner_id, now, now)
        )
        # 생성자를 admin 권한으로 자동 추가
        folder_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO shared_folder_members (folder_id, user_id, permission, created_at) VALUES (?, ?, 'admin', ?)",
            (folder_id, owner_id, now)
        )
        await db.commit()
        return folder_id


async def get_shared_folders_for_user(user_id: int, is_admin: bool = False) -> list:
    """사용자가 접근 가능한 공유 폴더 목록"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if is_admin:
            # 관리자는 모든 폴더 조회
            cursor = await db.execute(
                """SELECT sf.*, u.display_name as owner_name,
                      (SELECT COUNT(*) FROM voices WHERE shared_folder_id = sf.id AND folder_type = 'shared') as voice_count
                   FROM shared_folders sf
                   LEFT JOIN users u ON sf.owner_id = u.id
                   ORDER BY sf.updated_at DESC"""
            )
        else:
            cursor = await db.execute(
                """SELECT sf.*, u.display_name as owner_name, sfm.permission,
                      (SELECT COUNT(*) FROM voices WHERE shared_folder_id = sf.id AND folder_type = 'shared') as voice_count
                   FROM shared_folders sf
                   JOIN shared_folder_members sfm ON sf.id = sfm.folder_id AND sfm.user_id = ?
                   LEFT JOIN users u ON sf.owner_id = u.id
                   ORDER BY sf.updated_at DESC""",
                (user_id,)
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_shared_folder(folder_id: int) -> dict:
    """공유 폴더 단건 조회"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT sf.*, u.display_name as owner_name
               FROM shared_folders sf LEFT JOIN users u ON sf.owner_id = u.id
               WHERE sf.id = ?""",
            (folder_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_shared_folder(folder_id: int, name: str, description: str):
    """공유 폴더 수정"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE shared_folders SET name = ?, description = ?, updated_at = ? WHERE id = ?",
            (name, description, now_kst(), folder_id)
        )
        await db.commit()


async def delete_shared_folder(folder_id: int):
    """공유 폴더 삭제 (내부 음원도 삭제)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM voices WHERE shared_folder_id = ? AND folder_type = 'shared'", (folder_id,))
        await db.execute("DELETE FROM shared_folder_members WHERE folder_id = ?", (folder_id,))
        await db.execute("DELETE FROM shared_folders WHERE id = ?", (folder_id,))
        await db.commit()


async def get_shared_folder_voices(folder_id: int) -> list:
    """공유 폴더 내 음원 목록"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT v.*, u.display_name as owner_name
               FROM voices v LEFT JOIN users u ON v.user_id = u.id
               WHERE v.shared_folder_id = ? AND v.folder_type = 'shared'
               ORDER BY v.created_at DESC""",
            (folder_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def check_folder_permission(folder_id: int, user_id: int, is_admin: bool = False) -> str:
    """폴더 접근 권한 확인 -> 'admin'|'edit'|'view'|None"""
    if is_admin:
        return "admin"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT permission FROM shared_folder_members WHERE folder_id = ? AND user_id = ?",
            (folder_id, user_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def add_folder_member(folder_id: int, user_id: int, permission: str = "view"):
    """공유 폴더에 멤버 추가"""
    now = now_kst()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO shared_folder_members (folder_id, user_id, permission, created_at) VALUES (?, ?, ?, ?)",
                (folder_id, user_id, permission, now)
            )
        except Exception:
            # 이미 존재하면 업데이트
            await db.execute(
                "UPDATE shared_folder_members SET permission = ? WHERE folder_id = ? AND user_id = ?",
                (permission, folder_id, user_id)
            )
        await db.commit()


async def remove_folder_member(folder_id: int, user_id: int):
    """공유 폴더에서 멤버 제거"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM shared_folder_members WHERE folder_id = ? AND user_id = ?",
            (folder_id, user_id)
        )
        await db.commit()


async def get_folder_members(folder_id: int) -> list:
    """공유 폴더 멤버 목록"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT sfm.*, u.display_name, u.email
               FROM shared_folder_members sfm
               JOIN users u ON sfm.user_id = u.id
               WHERE sfm.folder_id = ?
               ORDER BY sfm.permission ASC""",
            (folder_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ── 배포 심사 CRUD ──

async def create_publish_review(voice_id: int, shared_folder_id: int, requested_by: int) -> int:
    """배포 심사 요청 생성 -> ID 반환"""
    now = now_kst()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO voice_publish_reviews (voice_id, shared_folder_id, requested_by, status, created_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (voice_id, shared_folder_id, requested_by, now)
        )
        # 음원 상태를 review로 변경
        await db.execute("UPDATE voices SET status = 'review' WHERE id = ?", (voice_id,))
        await db.commit()
        return cursor.lastrowid


async def get_pending_reviews(is_admin: bool = False, user_id: int = None) -> list:
    """대기 중인 배포 심사 목록 (관리자용)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT pr.*, v.name as voice_name, v.duration, v.file_path,
                      u.display_name as requester_name, sf.name as folder_name
               FROM voice_publish_reviews pr
               JOIN voices v ON pr.voice_id = v.id
               JOIN users u ON pr.requested_by = u.id
               LEFT JOIN shared_folders sf ON pr.shared_folder_id = sf.id
               WHERE pr.status = 'pending'
               ORDER BY pr.created_at DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def approve_publish_review(review_id: int, reviewed_by: int, review_note: str = ""):
    """배포 심사 승인 -> 음원을 배포 폴더로 이동"""
    now = now_kst()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM voice_publish_reviews WHERE id = ?", (review_id,))
        review = await cursor.fetchone()
        if not review:
            return None

        # 심사 상태 업데이트
        await db.execute(
            """UPDATE voice_publish_reviews SET status = 'approved', reviewed_by = ?,
               review_note = ?, reviewed_at = ? WHERE id = ?""",
            (reviewed_by, review_note, now, review_id)
        )

        # 음원을 published로 변경
        await db.execute(
            """UPDATE voices SET folder_type = 'published', status = 'published',
               can_generate = 1 WHERE id = ?""",
            (review["voice_id"],)
        )
        await db.commit()
        return dict(review)


async def reject_publish_review(review_id: int, reviewed_by: int, review_note: str = ""):
    """배포 심사 거절"""
    now = now_kst()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM voice_publish_reviews WHERE id = ?", (review_id,))
        review = await cursor.fetchone()
        if not review:
            return None

        await db.execute(
            """UPDATE voice_publish_reviews SET status = 'rejected', reviewed_by = ?,
               review_note = ?, reviewed_at = ? WHERE id = ?""",
            (reviewed_by, review_note, now, review_id)
        )
        # 음원 상태를 active로 복원
        await db.execute("UPDATE voices SET status = 'active' WHERE id = ?", (review["voice_id"],))
        await db.commit()
        return dict(review)


async def publish_voice_update(voice_id: int) -> bool:
    """배포된 음원 업데이트 (버전 증가)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT publish_version FROM voices WHERE id = ?", (voice_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        new_version = (row[0] or 1) + 1
        await db.execute(
            "UPDATE voices SET publish_version = ?, status = 'published' WHERE id = ?",
            (new_version, voice_id)
        )
        await db.commit()
        return True
