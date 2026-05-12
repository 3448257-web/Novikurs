"""
database.py — поддерживает оба режима:
  - SQLite    (локальная разработка, DATABASE_URL не задан)
  - PostgreSQL (Railway продакшен, DATABASE_URL задан автоматически)
"""
import os
import sqlite3

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

USE_POSTGRES = bool(DATABASE_URL)
SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'rehab.db')


def get_db():
    if USE_POSTGRES:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn
    else:
        os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def adapt(sql):
    """Convert ? placeholders to %s for PostgreSQL"""
    if USE_POSTGRES:
        return sql.replace('?', '%s')
    return sql


def db_exec(conn, sql, params=()):
    sql = adapt(sql)
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur
    return conn.execute(sql, params)


def commit(conn):
    if USE_POSTGRES:
        conn.commit()
    else:
        conn.commit()


SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',
    package TEXT DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    avatar_initials TEXT
);
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    package TEXT NOT NULL,
    started_at TEXT DEFAULT (datetime('now')),
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    week_num INTEGER NOT NULL,
    lesson_id TEXT NOT NULL,
    completed INTEGER DEFAULT 0,
    completed_at TEXT,
    score INTEGER DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    test_id TEXT NOT NULL,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    answers TEXT NOT NULL,
    completed_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    week_num INTEGER,
    mood INTEGER,
    content TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS group_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    duration_min INTEGER DEFAULT 60,
    package TEXT NOT NULL,
    meeting_url TEXT,
    week_num INTEGER,
    facilitator_id INTEGER REFERENCES users(id),
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS session_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES group_sessions(id),
    user_id INTEGER REFERENCES users(id),
    booked_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS individual_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER REFERENCES users(id),
    psychologist_id INTEGER REFERENCES users(id),
    scheduled_at TEXT NOT NULL,
    duration_min INTEGER DEFAULT 50,
    status TEXT DEFAULT 'scheduled',
    meeting_url TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS crisis_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    message TEXT,
    level TEXT DEFAULT 'medium',
    resolved INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    text TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',
    package TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    avatar_initials TEXT
);
CREATE TABLE IF NOT EXISTS enrollments (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    package TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT NOW(),
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS progress (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    week_num INTEGER NOT NULL,
    lesson_id TEXT NOT NULL,
    completed INTEGER DEFAULT 0,
    completed_at TIMESTAMP,
    score INTEGER DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS test_results (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    test_id TEXT NOT NULL,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    answers TEXT NOT NULL,
    completed_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS journal_entries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    week_num INTEGER,
    mood INTEGER,
    content TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS group_sessions (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    scheduled_at TIMESTAMP NOT NULL,
    duration_min INTEGER DEFAULT 60,
    package TEXT NOT NULL,
    meeting_url TEXT,
    week_num INTEGER,
    facilitator_id INTEGER REFERENCES users(id),
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS session_bookings (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES group_sessions(id),
    user_id INTEGER REFERENCES users(id),
    booked_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS individual_sessions (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES users(id),
    psychologist_id INTEGER REFERENCES users(id),
    scheduled_at TIMESTAMP NOT NULL,
    duration_min INTEGER DEFAULT 50,
    status TEXT DEFAULT 'scheduled',
    meeting_url TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS crisis_alerts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    message TEXT,
    level TEXT DEFAULT 'medium',
    resolved INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    text TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
"""


def init_db():
    conn = get_db()
    if USE_POSTGRES:
        schema = SCHEMA_POSTGRES
        cur = conn.cursor()
        for stmt in [s.strip() for s in schema.split(';') if s.strip()]:
            cur.execute(stmt)
        conn.commit()
        cur.close()
    else:
        conn.executescript(SCHEMA_SQLITE)
        conn.commit()
    conn.close()
    print(f"Database initialized ({'PostgreSQL' if USE_POSTGRES else 'SQLite'})")


def seed_demo_users():
    from werkzeug.security import generate_password_hash
    conn = get_db()
    users = [
        ('admin@rehab.ru',        generate_password_hash('admin123'),   'Администратор',  'admin',        None,      'АД'),
        ('student@rehab.ru',      generate_password_hash('student123'), 'Иван Петров',    'student',      'premium', 'ИП'),
        ('psychologist@rehab.ru', generate_password_hash('psych123'),   'Мария Соколова', 'psychologist', None,      'МС'),
    ]
    for email, pw, name, role, pkg, initials in users:
        try:
            db_exec(conn, "INSERT INTO users (email,password_hash,name,role,package,avatar_initials) VALUES (?,?,?,?,?,?)",
                    (email, pw, name, role, pkg, initials))
            commit(conn)
        except Exception:
            if USE_POSTGRES: conn.rollback()
    try:
        db_exec(conn, "INSERT INTO enrollments (user_id,package) SELECT id,package FROM users WHERE email='student@rehab.ru' AND package IS NOT NULL")
        commit(conn)
    except Exception:
        if USE_POSTGRES: conn.rollback()
    conn.close()
    print("Demo users seeded")


if __name__ == '__main__':
    init_db()
    seed_demo_users()
