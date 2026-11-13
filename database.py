import aiosqlite
from config import DB_PATH

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                package INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                data TEXT,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def get_or_create_user(user_id: int, username: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def add_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()

async def get_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def save_request(user_id: int, data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO requests (user_id, data, status) VALUES (?, ?, ?)",
            (user_id, json.dumps(data, ensure_ascii=False), "new")
        )
        await db.commit()
        return cursor.lastrowid

async def get_recent_requests(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, status, created_at FROM requests ORDER BY id DESC LIMIT ?",
            (limit,)
        ) as cursor:
            return await cursor.fetchall()

async def get_user_requests(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, status, created_at FROM requests WHERE user_id = ? ORDER BY id DESC",
            (user_id,)
        ) as cursor:
            return await cursor.fetchall()

async def get_request_by_id(request_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, data, status, created_at FROM requests WHERE id = ?",
            (request_id,)
        ) as cursor:
            return await cursor.fetchone()

async def set_request_status(request_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE requests SET status = ? WHERE id = ?",
            (status, request_id)
        )
        await db.commit()

async def set_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()

async def save_draft(user_id: int, data: dict, state: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS drafts (
                user_id INTEGER PRIMARY KEY,
                data TEXT,
                state TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "INSERT OR REPLACE INTO drafts (user_id, data, state) VALUES (?, ?, ?)",
            (user_id, json.dumps(data, ensure_ascii=False), state)
        )
        await db.commit()

async def load_draft(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT data, state FROM drafts WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0]), row[1]
            return None, None

async def save_support_ticket(user_id: int, request_number: str, description: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                request_number TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor = await db.execute(
            "INSERT INTO support_tickets (user_id, request_number, description) VALUES (?, ?, ?)",
            (user_id, request_number, description)
        )
        await db.commit()
        return cursor.lastrowid
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM drafts WHERE updated_at < datetime('now', ?)",
            (f'-{days} days',)
        )
        await db.commit()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM drafts WHERE user_id = ?", (user_id,))
        await db.commit()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, balance FROM users WHERE balance > 0 ORDER BY balance DESC"
        ) as cursor:
            return await cursor.fetchall()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT balance, created_at FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone()

async def deduct_balance(user_id: int, amount: int = 1) -> bool:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row or row[0] < amount:
                return False
        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()
        return True

async def get_stuck_requests(hours: int = 24):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT id, user_id FROM requests 
               WHERE status = 'new' AND created_at < datetime('now', ?)""",
            (f'-{hours} hours',)
        ) as cursor:
            return await cursor.fetchall()
