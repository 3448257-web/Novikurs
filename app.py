import os, json, sqlite3
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db, db_exec, commit, USE_POSTGRES, init_db, seed_demo_users

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'rehab-dev-secret-change-in-prod')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# Load program data
_data_path = os.path.join(os.path.dirname(__file__), 'data', 'program.json')
with open(_data_path, encoding='utf-8') as f:
    PROGRAM = json.load(f)


# ─── Helpers ─────────────────────────────────────────────────────

def _row(cursor_or_result):
    row = cursor_or_result.fetchone()
    if row is None:
        return None
    if USE_POSTGRES:
        return dict(row)
    return dict(row)   # sqlite3.Row also supports this

def _rows(cursor_or_result):
    rows = cursor_or_result.fetchall()
    if USE_POSTGRES:
        return [dict(r) for r in rows]
    return [dict(r) for r in rows]

def _scalar(cursor_or_result, key='n'):
    row = _row(cursor_or_result)
    return row[key] if row else 0


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('user_role') not in roles:
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def current_user():
    if 'user_id' not in session:
        return None
    db = get_db()
    u = _row(db_exec(db, "SELECT * FROM users WHERE id=?", (session['user_id'],)))
    db.close()
    return u

def get_user_progress(user_id):
    db = get_db()
    rows = _rows(db_exec(db, "SELECT lesson_id, completed FROM progress WHERE user_id=?", (user_id,)))
    db.close()
    return {r['lesson_id']: r['completed'] for r in rows}

def get_current_week(user_id):
    db = get_db()
    enrollment = _row(db_exec(db, "SELECT started_at FROM enrollments WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 1", (user_id,)))
    db.close()
    if not enrollment:
        return 1
    started_raw = enrollment['started_at']
    if isinstance(started_raw, str):
        started = datetime.fromisoformat(started_raw[:19])
    else:
        started = started_raw  # already datetime (postgres)
    days_elapsed = (datetime.now() - started).days
    return min(12, max(1, days_elapsed // 7 + 1))

def get_notifications(user_id, limit=10):
    db = get_db()
    notifs = _rows(db_exec(db, "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)))
    unread = _scalar(db_exec(db, "SELECT COUNT(*) as n FROM notifications WHERE user_id=? AND read=0", (user_id,)))
    db.close()
    return notifs, unread


# ─── Context processor ───────────────────────────────────────────

@app.context_processor
def inject_globals():
    unread = 0
    if 'user_id' in session:
        try:
            db = get_db()
            unread = _scalar(db_exec(db, "SELECT COUNT(*) as n FROM notifications WHERE user_id=? AND read=0", (session['user_id'],)))
            db.close()
        except Exception:
            pass
    return {'unread': unread}

app.jinja_env.globals['enumerate'] = enumerate
app.jinja_env.globals['now'] = datetime.now
app.jinja_env.globals['len'] = len


# ─── Public ──────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html', packages=PROGRAM['packages'])

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        db = get_db()
        user = _row(db_exec(db, "SELECT * FROM users WHERE email=?", (email,)))
        db.close()
        if user and check_password_hash(user['password_hash'], password):
            session.permanent = True
            session['user_id'] = user['id']
            session['user_role'] = user['role']
            session['user_name'] = user['name']
            session['user_package'] = user['package']
            return redirect(request.args.get('next', url_for('dashboard')))
        error = 'Неверный email или пароль'
    return render_template('auth/login.html', error=error)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    error = None
    if request.method == 'POST':
        name    = request.form.get('name', '').strip()
        email   = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        package  = request.form.get('package', 'basic')
        if len(password) < 8:
            error = 'Пароль должен быть не менее 8 символов'
        elif not name or not email:
            error = 'Заполните все поля'
        else:
            parts = name.split()
            initials = (parts[0][0] + (parts[1][0] if len(parts) > 1 else '')).upper()
            db = get_db()
            try:
                if USE_POSTGRES:
                    cur = db_exec(db, """
                        INSERT INTO users (email,password_hash,name,role,package,avatar_initials)
                        VALUES (?,?,?,?,?,?) RETURNING id
                    """, (email, generate_password_hash(password), name, 'student', package, initials))
                    commit(db)
                    user_id = cur.fetchone()['id']
                else:
                    cur = db_exec(db, "INSERT INTO users (email,password_hash,name,role,package,avatar_initials) VALUES (?,?,?,?,?,?)",
                        (email, generate_password_hash(password), name, 'student', package, initials))
                    commit(db)
                    user_id = cur.lastrowid

                db_exec(db, "INSERT INTO enrollments (user_id,package) VALUES (?,?)", (user_id, package))
                db_exec(db, "INSERT INTO notifications (user_id,text) VALUES (?,?)",
                    (user_id, f'Добро пожаловать, {name}! Ваш курс начался.'))
                commit(db)
                db.close()
                session.permanent = True
                session['user_id'] = user_id
                session['user_role'] = 'student'
                session['user_name'] = name
                session['user_package'] = package
                return redirect(url_for('dashboard'))
            except Exception as e:
                db.close()
                if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                    error = 'Этот email уже зарегистрирован'
                else:
                    error = f'Ошибка регистрации: {e}'
    return render_template('auth/register.html', error=error, packages=PROGRAM['packages'])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# ─── Dashboard ───────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    user = current_user()
    progress = get_user_progress(user['id'])
    current_week = get_current_week(user['id'])
    notifs, unread = get_notifications(user['id'])

    total_lessons = sum(len(w['lessons']) for m in PROGRAM['months'] for w in m['weeks'])
    completed = sum(1 for v in progress.values() if v)
    pct = round(completed / total_lessons * 100) if total_lessons else 0

    cur_week_data = None
    for m in PROGRAM['months']:
        for w in m['weeks']:
            if w['num'] == current_week:
                cur_week_data = dict(w)
                cur_week_data['month_title'] = m['title']
                cur_week_data['month_color'] = m['color']
                break

    db = get_db()
    entries = _rows(db_exec(db, "SELECT * FROM journal_entries WHERE user_id=? ORDER BY created_at DESC LIMIT 3", (user['id'],)))
    upcoming = _rows(db_exec(db, """
        SELECT gs.*, COUNT(sb.id) as booked_count
        FROM group_sessions gs
        LEFT JOIN session_bookings sb ON sb.session_id=gs.id
        WHERE gs.active=1 AND (gs.package=? OR gs.package='all')
        GROUP BY gs.id ORDER BY gs.scheduled_at LIMIT 3
    """, (user['package'] or 'basic',)))
    db.close()

    return render_template('dashboard/index.html',
        user=user, progress=progress, current_week=current_week,
        cur_week_data=cur_week_data, total_lessons=total_lessons,
        completed=completed, pct=pct, program=PROGRAM,
        notifs=notifs, unread=unread, entries=entries,
        upcoming_sessions=upcoming, packages=PROGRAM['packages'])


# ─── Course ──────────────────────────────────────────────────────

@app.route('/course')
@login_required
def course():
    user = current_user()
    return render_template('dashboard/course.html',
        user=user, program=PROGRAM,
        progress=get_user_progress(user['id']),
        current_week=get_current_week(user['id']),
        packages=PROGRAM['packages'])

@app.route('/lesson/<lesson_id>')
@login_required
def lesson(lesson_id):
    user = current_user()
    lesson_data = week_data = month_data = None
    for m in PROGRAM['months']:
        for w in m['weeks']:
            for l in w['lessons']:
                if l['id'] == lesson_id:
                    lesson_data, week_data, month_data = l, w, m
                    break
    if not lesson_data:
        return redirect(url_for('course'))

    all_ids = [l['id'] for m in PROGRAM['months'] for w in m['weeks'] for l in w['lessons']]
    idx = all_ids.index(lesson_id) if lesson_id in all_ids else None
    next_id = all_ids[idx + 1] if idx is not None and idx + 1 < len(all_ids) else None

    return render_template('dashboard/lesson.html',
        user=user, lesson=lesson_data, week=week_data, month=month_data,
        progress=get_user_progress(user['id']), next_lesson_id=next_id)

@app.route('/api/complete-lesson', methods=['POST'])
@login_required
def complete_lesson():
    data = request.json
    uid = session['user_id']
    db = get_db()
    existing = _row(db_exec(db, "SELECT id FROM progress WHERE user_id=? AND lesson_id=?", (uid, data['lesson_id'])))
    if existing:
        db_exec(db, "UPDATE progress SET completed=1, completed_at=? WHERE user_id=? AND lesson_id=?",
            (datetime.now().isoformat(), uid, data['lesson_id']))
    else:
        db_exec(db, "INSERT INTO progress (user_id,week_num,lesson_id,completed,completed_at) VALUES (?,?,?,1,?)",
            (uid, data.get('week_num', 1), data['lesson_id'], datetime.now().isoformat()))
    commit(db)
    total = _scalar(db_exec(db, "SELECT COUNT(*) as n FROM progress WHERE user_id=? AND completed=1", (uid,)))
    db.close()
    return jsonify({'ok': True, 'total_completed': total})


# ─── Tests ───────────────────────────────────────────────────────

@app.route('/test/<test_id>')
@login_required
def test_page(test_id):
    user = current_user()
    test_data = PROGRAM.get('tests', {}).get(test_id)
    if not test_data:
        return redirect(url_for('course'))
    db = get_db()
    prev = _row(db_exec(db, "SELECT * FROM test_results WHERE user_id=? AND test_id=? ORDER BY completed_at DESC LIMIT 1",
        (user['id'], test_id)))
    db.close()
    return render_template('dashboard/test.html', user=user, test=test_data, test_id=test_id, prev_result=prev)

@app.route('/api/submit-test', methods=['POST'])
@login_required
def submit_test():
    data = request.json
    test_id = data.get('test_id')
    answers = data.get('answers', {})
    test_data = PROGRAM.get('tests', {}).get(test_id)
    if not test_data:
        return jsonify({'ok': False})
    total = len(test_data['questions'])
    score = sum(int(v) for v in answers.values())
    uid = session['user_id']
    db = get_db()
    db_exec(db, "INSERT INTO test_results (user_id,test_id,score,total,answers) VALUES (?,?,?,?,?)",
        (uid, test_id, score, total * 4, json.dumps(answers)))
    commit(db)
    db.close()
    return jsonify({'ok': True, 'score': score, 'total': total * 4, 'pct': round(score / (total * 4) * 100)})


# ─── Journal ─────────────────────────────────────────────────────

@app.route('/journal', methods=['GET', 'POST'])
@login_required
def journal():
    user = current_user()
    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        mood = int(request.form.get('mood', 3))
        if content:
            db = get_db()
            db_exec(db, "INSERT INTO journal_entries (user_id,week_num,mood,content) VALUES (?,?,?,?)",
                (user['id'], get_current_week(user['id']), mood, content))
            commit(db)
            db.close()
        return redirect(url_for('journal'))
    db = get_db()
    entries = _rows(db_exec(db, "SELECT * FROM journal_entries WHERE user_id=? ORDER BY created_at DESC", (user['id'],)))
    db.close()
    return render_template('dashboard/journal.html', user=user, entries=entries)


# ─── Sessions ────────────────────────────────────────────────────

@app.route('/sessions')
@login_required
def sessions_page():
    user = current_user()
    db = get_db()
    group_sessions = _rows(db_exec(db, """
        SELECT gs.*, COUNT(sb.id) as booked_count,
        (SELECT COUNT(*) FROM session_bookings WHERE session_id=gs.id AND user_id=?) as is_booked
        FROM group_sessions gs
        LEFT JOIN session_bookings sb ON sb.session_id=gs.id
        WHERE gs.active=1 AND (gs.package=? OR gs.package='all')
        GROUP BY gs.id ORDER BY gs.scheduled_at
    """, (user['id'], user['package'] or 'basic')))
    ind_sessions = _rows(db_exec(db, """
        SELECT s.*, u.name as psychologist_name
        FROM individual_sessions s JOIN users u ON u.id=s.psychologist_id
        WHERE s.student_id=? ORDER BY s.scheduled_at DESC
    """, (user['id'],)))
    db.close()
    return render_template('dashboard/sessions.html',
        user=user, group_sessions=group_sessions, ind_sessions=ind_sessions)

@app.route('/api/book-session', methods=['POST'])
@login_required
def book_session():
    session_id = request.json.get('session_id')
    uid = session['user_id']
    db = get_db()
    existing = _row(db_exec(db, "SELECT id FROM session_bookings WHERE session_id=? AND user_id=?", (session_id, uid)))
    if not existing:
        db_exec(db, "INSERT INTO session_bookings (session_id,user_id) VALUES (?,?)", (session_id, uid))
        commit(db)
    db.close()
    return jsonify({'ok': True})


# ─── Progress ────────────────────────────────────────────────────

@app.route('/progress')
@login_required
def progress():
    user = current_user()
    prog = get_user_progress(user['id'])
    current_week = get_current_week(user['id'])
    db = get_db()
    mood_data = _rows(db_exec(db, "SELECT mood, created_at FROM journal_entries WHERE user_id=? ORDER BY created_at DESC LIMIT 30", (user['id'],)))
    test_results = _rows(db_exec(db, "SELECT * FROM test_results WHERE user_id=? ORDER BY completed_at DESC", (user['id'],)))
    db.close()
    week_stats = []
    for m in PROGRAM['months']:
        for w in m['weeks']:
            total = len(w['lessons'])
            done = sum(1 for l in w['lessons'] if prog.get(l['id']))
            week_stats.append({'week': w['num'], 'title': w['title'], 'done': done, 'total': total,
                'pct': round(done / total * 100) if total else 0})
    return render_template('dashboard/progress.html',
        user=user, prog=prog, current_week=current_week,
        week_stats=week_stats, mood_data=mood_data, test_results=test_results, program=PROGRAM)


# ─── SOS / Crisis ────────────────────────────────────────────────

@app.route('/api/crisis', methods=['POST'])
@login_required
def crisis():
    data = request.json
    uid = session['user_id']
    db = get_db()
    db_exec(db, "INSERT INTO crisis_alerts (user_id,message,level) VALUES (?,?,?)",
        (uid, data.get('message', ''), data.get('level', 'medium')))
    psychs = _rows(db_exec(db, "SELECT id FROM users WHERE role='psychologist'"))
    for p in psychs:
        db_exec(db, "INSERT INTO notifications (user_id,text) VALUES (?,?)",
            (p['id'], f'КРИЗИС: {session["user_name"]} нуждается в экстренной помощи!'))
    commit(db)
    db.close()
    return jsonify({'ok': True, 'message': 'Сигнал принят. Психолог свяжется с вами в течение 30 минут.'})


# ─── Admin ───────────────────────────────────────────────────────

@app.route('/admin')
@role_required('admin')
def admin():
    db = get_db()
    users = _rows(db_exec(db, "SELECT * FROM users ORDER BY created_at DESC"))
    total_users = sum(1 for u in users if u['role'] == 'student')
    alerts = _rows(db_exec(db, """
        SELECT ca.*, u.name FROM crisis_alerts ca JOIN users u ON u.id=ca.user_id
        WHERE ca.resolved=0 ORDER BY ca.created_at DESC
    """))
    enrollments_by_pkg = _rows(db_exec(db, "SELECT package, COUNT(*) as n FROM enrollments WHERE active=1 GROUP BY package"))
    db.close()
    return render_template('admin/index.html',
        users=users, total_users=total_users,
        alerts=alerts, enrollments_by_pkg=enrollments_by_pkg)


# ─── Notifications ───────────────────────────────────────────────

@app.route('/api/notifications/read', methods=['POST'])
@login_required
def mark_read():
    db = get_db()
    db_exec(db, "UPDATE notifications SET read=1 WHERE user_id=?", (session['user_id'],))
    commit(db)
    db.close()
    return jsonify({'ok': True})


# ─── Startup ─────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    seed_demo_users()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
