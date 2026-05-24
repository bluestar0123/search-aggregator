"""SQLite 数据库 - 异步操作，管理用户、API Key、用量日志"""
from __future__ import annotations

import aiosqlite
from pathlib import Path

# 数据库文件位于项目根目录/data/search.db
_BASE_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DB_PATH = _DATA_DIR / "search.db"


# ============================================================
# 数据库初始化
# ============================================================

async def init_db() -> None:
    """创建所有数据表。

    单表统一认证模型：所有用户（admin/normal）统一存 api_users。
    启动时自动删除旧 admin_users 表（兼容升级）。
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(str(_DB_PATH))
    db.row_factory = aiosqlite.Row  # type: ignore[assignment]

    # 启用外键约束
    await db.execute("PRAGMA foreign_keys = ON")

    # --- 兼容迁移：删除旧 admin_users 表 ---
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='admin_users'"
    )
    if await cursor.fetchone():
        await db.execute("DROP TABLE IF EXISTS admin_users")

    # --- 创建表 ---
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS api_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            balance REAL DEFAULT 0.0,
            role TEXT DEFAULT 'normal',
            quota_per_day INTEGER DEFAULT 100,
            quota_per_month INTEGER DEFAULT 1000,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key_prefix TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            quota_per_day INTEGER DEFAULT 100,
            quota_per_month INTEGER DEFAULT 3000,
            price_per_call REAL DEFAULT 0.01,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES api_users(id)
        );

        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            channels TEXT DEFAULT '',
            results_count INTEGER DEFAULT 0,
            latency_ms REAL DEFAULT 0,
            cost REAL DEFAULT 0,
            status TEXT DEFAULT 'ok',
            error_msg TEXT DEFAULT NULL,
            ip_address TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
            FOREIGN KEY (user_id) REFERENCES api_users(id)
        );
    """)

    # 性能索引：加速 check_quota 和日志查询
    await db.execute("CREATE INDEX IF NOT EXISTS idx_usage_key_date ON usage_logs(api_key_id, created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_usage_user_date ON usage_logs(user_id, created_at)")

    # 确保默认 admin 用户存在
    import bcrypt
    cursor = await db.execute(
        "SELECT id FROM api_users WHERE username = ?", ("admin",)
    )
    row = await cursor.fetchone()
    if row is None:
        password_hash = bcrypt.hashpw(
            b"admin123", bcrypt.gensalt()
        ).decode("utf-8")
        await db.execute(
            "INSERT INTO api_users (username, password_hash, role, balance) VALUES (?, ?, ?, ?)",
            ("admin", password_hash, "admin", 100.0),
        )

    # 确保 role 列存在（兼容旧数据库）
    cursor = await db.execute("PRAGMA table_info(api_users)")
    columns = [row["name"] for row in await cursor.fetchall()]
    if "role" not in columns:
        await db.execute("ALTER TABLE api_users ADD COLUMN role TEXT DEFAULT 'normal'")

    await db.commit()
    await db.close()


# ============================================================
# 获取数据库连接
# ============================================================

async def get_db() -> aiosqlite.Connection:
    """获取一个新的 aiosqlite 连接，使用后需 close。

    每次调用都新建连接（非单例），保证异步安全。
    调用方应使用 try/finally 确保连接关闭。
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(_DB_PATH))
    db.row_factory = aiosqlite.Row  # type: ignore[assignment]
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db
