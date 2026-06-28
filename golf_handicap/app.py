import sqlite3
import os
import csv
import io
import json
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session, Response
from datetime import date
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-in-prod')

DB_PATH = os.environ.get('DB_PATH', os.path.expanduser('~/golf_handicap.db'))

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def init_db():
    conn = get_db()
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS golfer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            password_hash TEXT,
            member_since DATE DEFAULT (date('now'))
        );
        CREATE TABLE IF NOT EXISTS course (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            city TEXT,
            country TEXT,
            holes INTEGER DEFAULT 18
        );
        CREATE TABLE IF NOT EXISTS tee_set (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER REFERENCES course(id),
            tee_name TEXT NOT NULL,
            gender TEXT CHECK (gender IN ('M','F','Any')),
            course_rating REAL NOT NULL,
            slope_rating INTEGER NOT NULL,
            par INTEGER NOT NULL,
            front_rating REAL,
            front_slope INTEGER,
            back_rating REAL,
            back_slope INTEGER,
            hole_handicaps TEXT
        );
        CREATE TABLE IF NOT EXISTS round (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            golfer_id INTEGER REFERENCES golfer(id),
            tee_set_id INTEGER REFERENCES tee_set(id),
            played_on DATE NOT NULL,
            holes_played INTEGER DEFAULT 18,
            nine TEXT CHECK (nine IN ('front','back')),
            adjusted_gross_score INTEGER NOT NULL,
            actual_gross_score INTEGER,
            score_differential REAL,
            exceptional_reduction REAL DEFAULT 0,
            weather_notes TEXT
        );
        CREATE TABLE IF NOT EXISTS hole_score (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER REFERENCES round(id),
            hole_number INTEGER CHECK (hole_number BETWEEN 1 AND 18),
            par INTEGER,
            strokes INTEGER NOT NULL,
            adjusted_strokes INTEGER,
            putts INTEGER
        );
        CREATE TABLE IF NOT EXISTS hole (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tee_set_id INTEGER REFERENCES tee_set(id),
            hole_number INTEGER CHECK (hole_number BETWEEN 1 AND 18),
            par INTEGER CHECK (par BETWEEN 3 AND 5),
            handicap INTEGER CHECK (handicap BETWEEN 1 AND 18),
            UNIQUE(tee_set_id, hole_number)
        );
        CREATE TABLE IF NOT EXISTS handicap_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            golfer_id INTEGER REFERENCES golfer(id),
            round_id INTEGER REFERENCES round(id),
            calculated_on DATE NOT NULL,
            handicap_index REAL NOT NULL,
            rounds_used INTEGER NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS calc_differential
        AFTER INSERT ON round
        BEGIN
            UPDATE round
            SET score_differential = ROUND(
                (NEW.adjusted_gross_score - (SELECT course_rating FROM tee_set WHERE id = NEW.tee_set_id))
                * 113.0 / (SELECT slope_rating FROM tee_set WHERE id = NEW.tee_set_id),
            1)
            WHERE id = NEW.id;
        END;
    ''')
    conn.commit()
    conn.close()

def migrate_db():
    """Add new columns to existing databases."""
    conn = get_db()
    snap_cols = [row[1] for row in conn.execute("PRAGMA table_info(handicap_snapshot)").fetchall()]
    if 'round_id' not in snap_cols:
        conn.execute("ALTER TABLE handicap_snapshot ADD COLUMN round_id INTEGER REFERENCES round(id)")

    round_cols = [row[1] for row in conn.execute("PRAGMA table_info(round)").fetchall()]
    if 'actual_gross_score' not in round_cols:
        conn.execute("ALTER TABLE round ADD COLUMN actual_gross_score INTEGER")
        conn.execute("UPDATE round SET actual_gross_score = adjusted_gross_score WHERE actual_gross_score IS NULL")
    if 'exceptional_reduction' not in round_cols:
        conn.execute("ALTER TABLE round ADD COLUMN exceptional_reduction REAL DEFAULT 0")
        conn.execute("UPDATE round SET exceptional_reduction = 0 WHERE exceptional_reduction IS NULL")
    if 'holes_played' not in round_cols:
        conn.execute("ALTER TABLE round ADD COLUMN holes_played INTEGER DEFAULT 18")
        conn.execute("UPDATE round SET holes_played = 18 WHERE holes_played IS NULL")
    if 'nine' not in round_cols:
        conn.execute("ALTER TABLE round ADD COLUMN nine TEXT")

    tee_cols = [row[1] for row in conn.execute("PRAGMA table_info(tee_set)").fetchall()]
    for col in ['front_rating', 'back_rating']:
        if col not in tee_cols:
            conn.execute(f"ALTER TABLE tee_set ADD COLUMN {col} REAL")
    for col in ['front_slope', 'back_slope']:
        if col not in tee_cols:
            conn.execute(f"ALTER TABLE tee_set ADD COLUMN {col} INTEGER")

    golfer_cols = [row[1] for row in conn.execute("PRAGMA table_info(golfer)").fetchall()]
    if 'password_hash' not in golfer_cols:
        conn.execute("ALTER TABLE golfer ADD COLUMN password_hash TEXT")

    hole_cols = [row[1] for row in conn.execute("PRAGMA table_info(hole_score)").fetchall()]
    if 'adjusted_strokes' not in hole_cols:
        conn.execute("ALTER TABLE hole_score ADD COLUMN adjusted_strokes INTEGER")
        # Backfill: existing strokes are the adjusted values, copy them
        conn.execute("UPDATE hole_score SET adjusted_strokes = strokes WHERE adjusted_strokes IS NULL")

    conn.commit()
    conn.close()

def get_nine_hole_rating(tee, nine):
    """Return (rating, slope, par) for a specific nine.
    Uses explicit front/back values if set, otherwise halves the 18-hole rating."""
    if nine == 'front' and tee['front_rating'] and tee['front_slope']:
        rating = tee['front_rating']
        slope = tee['front_slope']
    elif nine == 'back' and tee['back_rating'] and tee['back_slope']:
        rating = tee['back_rating']
        slope = tee['back_slope']
    else:
        # Fallback: half the 18-hole rating, same slope
        rating = tee['course_rating'] / 2.0
        slope = tee['slope_rating']

    # 9-hole par from hole data or half total
    return rating, slope, tee['par'] // 2

def save_snapshot(conn, golfer_id, round_id, played_on):
    """Create or update a handicap snapshot tied to a specific round."""
    handicap, rounds_used, _ = calculate_handicap(golfer_id)
    if handicap is not None:
        existing = conn.execute(
            'SELECT id FROM handicap_snapshot WHERE round_id = ?', (round_id,)
        ).fetchone()
        if existing:
            conn.execute('''
                UPDATE handicap_snapshot
                SET handicap_index = ?, rounds_used = ?, calculated_on = ?
                WHERE id = ?
            ''', (handicap, rounds_used, played_on, existing['id']))
        else:
            conn.execute('''
                INSERT INTO handicap_snapshot (golfer_id, round_id, calculated_on, handicap_index, rounds_used)
                VALUES (?, ?, ?, ?, ?)
            ''', (golfer_id, round_id, played_on, handicap, rounds_used))

def check_exceptional_score(conn, golfer_id, round_id):
    """WHS Exceptional Score Reduction: when a differential is far below
    the player's index at the time of play, apply -1 or -2 to all 20
    most recent differentials. New rounds posted later won't carry the
    adjustment, so the effect fades as adjusted rounds age out."""
    handicap, _, _ = calculate_handicap(golfer_id)
    if handicap is None:
        return

    row = conn.execute('SELECT score_differential FROM round WHERE id = ?', (round_id,)).fetchone()
    if not row or row['score_differential'] is None:
        return

    gap = handicap - row['score_differential']
    if gap >= 10.0:
        reduction = -2.0
    elif gap >= 7.0:
        reduction = -1.0
    else:
        return

    # Apply to all 20 most recent rounds — take the stronger (more negative) reduction
    recent_ids = [r['id'] for r in conn.execute('''
        SELECT id FROM round WHERE golfer_id = ?
        ORDER BY played_on DESC LIMIT 20
    ''', (golfer_id,)).fetchall()]

    for rid in recent_ids:
        conn.execute('''
            UPDATE round SET exceptional_reduction = MIN(exceptional_reduction, ?)
            WHERE id = ? AND (exceptional_reduction IS NULL OR exceptional_reduction > ?)
        ''', (reduction, rid, reduction))

def calculate_handicap(golfer_id):
    conn = get_db()
    rows = conn.execute('''
        SELECT id, score_differential, exceptional_reduction FROM round
        WHERE golfer_id = ?
        ORDER BY played_on DESC LIMIT 20
    ''', (golfer_id,)).fetchall()
    conn.close()

    valid = [(r['id'], r['score_differential'], r['exceptional_reduction'] or 0)
             for r in rows if r['score_differential'] is not None]
    n = len(valid)
    if n < 3:
        # USGA max index of 54.0 until enough rounds are posted
        return 54.0, n, set()

    # WHS sliding scale
    use = {3:1, 4:1, 5:1, 6:2, 7:2, 8:2, 9:3, 10:3, 11:3, 12:4, 13:4, 14:4,
           15:5, 16:5, 17:6, 18:6, 19:7}.get(n, 8)

    # Apply exceptional reduction to each differential before sorting
    adjusted_valid = [(rid, diff + red, red) for rid, diff, red in valid]

    # Sort by adjusted differential ascending; take the best `use` rounds
    sorted_valid = sorted(adjusted_valid, key=lambda x: x[1])
    used_ids = {row[0] for row in sorted_valid[:use]}

    avg = sum(row[1] for row in sorted_valid[:use]) / use

    # WHS sliding scale adjustments for small samples
    adjustment = {3: -2.0, 4: -1.0, 6: -1.0}.get(n, 0)

    return round((avg + adjustment) * 0.96, 1), n, used_ids

# ── Admin helpers ───────────────────────────────────────────────────────────

def get_admin_password():
    return os.environ.get('ADMIN_PASSWORD', '').strip()

def admin_enabled():
    return bool(get_admin_password())

def require_admin():
    """Returns redirect if admin protection is active and user is not authenticated."""
    if admin_enabled() and not session.get('admin'):
        return redirect(url_for('admin_login', next=request.path))
    return None

@app.context_processor
def inject_admin():
    # is_admin=True when no password is configured (open) or when authenticated
    return dict(
        is_admin=not admin_enabled() or bool(session.get('admin')),
        admin_enabled=admin_enabled()
    )

# ── Routes ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    conn = get_db()
    golfers = conn.execute('SELECT * FROM golfer ORDER BY name').fetchall()
    conn.close()
    return render_template('index.html', golfers=golfers)

@app.route('/rules')
def rules():
    return render_template('rules.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if not admin_enabled():
        return redirect(url_for('courses'))
    if session.get('admin'):
        return redirect(url_for('courses'))
    if request.method == 'POST':
        if request.form.get('password') == get_admin_password():
            session['admin'] = True
            next_url = request.form.get('next') or url_for('courses')
            return redirect(next_url)
        flash('Incorrect admin password.', 'danger')
    return render_template('admin_login.html', next=request.args.get('next', ''))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    flash('Admin logged out.', 'info')
    return redirect(url_for('courses'))

@app.route('/golfer/add', methods=['POST'])
def add_golfer():
    name = request.form['name'].strip()
    email = request.form.get('email', '').strip() or None
    password = request.form.get('password', '').strip()
    pw_hash = generate_password_hash(password) if password else None
    if name:
        conn = get_db()
        conn.execute('INSERT INTO golfer (name, email, password_hash) VALUES (?, ?, ?)', (name, email, pw_hash))
        conn.commit()
        conn.close()
        flash(f'Golfer "{name}" added.', 'success')
    return redirect(url_for('index'))

@app.route('/golfer/<int:golfer_id>/password', methods=['POST'])
def change_password(golfer_id):
    blocked = require_golfer_access(golfer_id)
    if blocked:
        return blocked
    conn = get_db()
    golfer = conn.execute('SELECT * FROM golfer WHERE id = ?', (golfer_id,)).fetchone()
    if not golfer:
        conn.close()
        flash('Golfer not found.', 'danger')
        return redirect(url_for('index'))

    current = request.form.get('current_password', '').strip()
    new_pw = request.form.get('new_password', '').strip()

    # If a password is already set, verify the current one
    if golfer['password_hash']:
        if not check_password_hash(golfer['password_hash'], current):
            conn.close()
            flash('Current password is incorrect.', 'danger')
            return redirect(url_for('dashboard', golfer_id=golfer_id))

    if new_pw:
        pw_hash = generate_password_hash(new_pw)
        conn.execute('UPDATE golfer SET password_hash = ? WHERE id = ?', (pw_hash, golfer_id))
        flash('Password updated.', 'success')
    else:
        # Clear password
        conn.execute('UPDATE golfer SET password_hash = NULL WHERE id = ?', (golfer_id,))
        session.pop(f'golfer_{golfer_id}', None)
        flash('Password removed.', 'info')
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard', golfer_id=golfer_id))

@app.route('/golfer/<int:golfer_id>/login', methods=['GET', 'POST'])
def golfer_login(golfer_id):
    conn = get_db()
    golfer = conn.execute('SELECT * FROM golfer WHERE id = ?', (golfer_id,)).fetchone()
    conn.close()
    if not golfer:
        flash('Golfer not found.', 'danger')
        return redirect(url_for('index'))
    golfer = dict(golfer)

    # No password set — go straight to dashboard
    if not golfer.get('password_hash'):
        session[f'golfer_{golfer_id}'] = True
        return redirect(url_for('dashboard', golfer_id=golfer_id))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if check_password_hash(golfer['password_hash'], password):
            session[f'golfer_{golfer_id}'] = True
            return redirect(url_for('dashboard', golfer_id=golfer_id))
        else:
            flash('Incorrect password.', 'danger')

    return render_template('login.html', golfer=golfer)

@app.route('/golfer/<int:golfer_id>/logout')
def golfer_logout(golfer_id):
    session.pop(f'golfer_{golfer_id}', None)
    flash('Logged out.', 'info')
    return redirect(url_for('index'))

def require_golfer_access(golfer_id):
    """Check if the session has access to this golfer. Returns redirect if not."""
    conn = get_db()
    golfer = conn.execute('SELECT password_hash FROM golfer WHERE id = ?', (golfer_id,)).fetchone()
    conn.close()
    if golfer and golfer['password_hash'] and not session.get(f'golfer_{golfer_id}'):
        return redirect(url_for('golfer_login', golfer_id=golfer_id))
    return None

@app.route('/golfer/<int:golfer_id>')
def dashboard(golfer_id):
    blocked = require_golfer_access(golfer_id)
    if blocked:
        return blocked
    conn = get_db()
    golfer = conn.execute('SELECT * FROM golfer WHERE id = ?', (golfer_id,)).fetchone()
    golfer = dict(golfer) if golfer else None
    
    # Get rounds with course info
    rounds_data = conn.execute('''
        SELECT r.*, c.name as course_name, t.tee_name, t.par as par,
               t.course_rating, t.slope_rating
        FROM round r
        JOIN tee_set t ON r.tee_set_id = t.id
        JOIN course c ON t.course_id = c.id
        WHERE r.golfer_id = ? AND r.played_on >= date('now', '-1 year')
        ORDER BY r.played_on DESC
    ''', (golfer_id,)).fetchall()
    
    rounds = []
    for r in rounds_data:
        round_dict = dict(r)
        
        hole_scores_data = conn.execute('''
            SELECT hole_number, strokes, adjusted_strokes
            FROM hole_score
            WHERE round_id = ?
        ''', (round_dict['id'],)).fetchall()

        # hole_scores: actual strokes (for editing); adjusted_scores: ESC-capped
        round_dict['hole_scores'] = {row['hole_number']: row['strokes']
                                     for row in hole_scores_data}
        round_dict['adjusted_scores'] = {row['hole_number']: row['adjusted_strokes']
                                         for row in hole_scores_data}
        # ===================================
        
        rounds.append(round_dict)
    
    # Get courses for the "Record a Round" form
    courses_data = conn.execute('''
        SELECT c.id, c.name, c.city, c.country, c.holes, 
               COUNT(t.id) as tee_count 
        FROM course c 
        LEFT JOIN tee_set t ON c.id = t.course_id 
        GROUP BY c.id 
        ORDER BY c.name
    ''').fetchall()
    courses = [dict(c) for c in courses_data]
    for c in courses:
        tees = conn.execute('SELECT id, tee_name FROM tee_set WHERE course_id = ? ORDER BY course_rating DESC', (c['id'],)).fetchall()
        c['tees'] = [dict(t) for t in tees]

    snapshots_data = conn.execute('''
        SELECT calculated_on, handicap_index, rounds_used
        FROM handicap_snapshot
        WHERE golfer_id = ? AND calculated_on >= date('now', '-1 year')
        ORDER BY calculated_on DESC, id DESC
        LIMIT 10
    ''', (golfer_id,)).fetchall()
    snapshots = [dict(s) for s in snapshots_data]

    # Low Handicap Index from the past 365 days (WHS soft/hard cap)
    low_row = conn.execute('''
        SELECT MIN(handicap_index) as low_index
        FROM handicap_snapshot
        WHERE golfer_id = ? AND calculated_on >= date('now', '-1 year')
    ''', (golfer_id,)).fetchone()
    low_index = low_row['low_index'] if low_row else None

    conn.close()

    handicap, rounds_used, used_round_ids = calculate_handicap(golfer_id)

    # Apply WHS soft cap / hard cap based on Low HI
    capped_handicap = handicap
    if handicap is not None and low_index is not None:
        diff = handicap - low_index
        if diff > 5.0:
            # Hard cap: cannot exceed Low HI + 5.0
            capped_handicap = round(low_index + 5.0, 1)
        elif diff > 3.0:
            # Soft cap: 50% of excess above 3.0
            capped_handicap = round(low_index + 3.0 + (diff - 3.0) * 0.5, 1)

    return render_template('dashboard.html',
        golfer=golfer,
        rounds=rounds,
        courses=courses,
        handicap=capped_handicap,
        uncapped_handicap=handicap,
        low_index=low_index,
        rounds_used=rounds_used,
        used_round_ids=used_round_ids,
        snapshots=snapshots,
        today=date.today().isoformat()
    )

def calculate_handicap_as_of(rounds_up_to):
    """Calculate handicap from a list of rounds (already filtered and ordered by date)."""
    valid = [(r['id'], r['score_differential'], r['exceptional_reduction'] or 0)
             for r in rounds_up_to if r['score_differential'] is not None]
    n = len(valid)
    if n < 3:
        return 54.0, n

    # Only use most recent 20
    valid = valid[-20:]
    n = len(valid)

    use = {3:1, 4:1, 5:1, 6:2, 7:2, 8:2, 9:3, 10:3, 11:3, 12:4, 13:4, 14:4,
           15:5, 16:5, 17:6, 18:6, 19:7}.get(n, 8)

    adjusted = [(rid, diff + red, red) for rid, diff, red in valid]
    sorted_valid = sorted(adjusted, key=lambda x: x[1])
    avg = sum(row[1] for row in sorted_valid[:use]) / use
    adjustment = {3: -2.0, 4: -1.0, 6: -1.0}.get(n, 0)
    return round((avg + adjustment) * 0.96, 1), n


@app.route('/golfer/<int:golfer_id>/recalculate', methods=['POST'])
def recalculate_handicap(golfer_id):
    blocked = require_golfer_access(golfer_id)
    if blocked:
        return blocked
    conn = get_db()

    all_rounds = conn.execute('''
        SELECT id, played_on, score_differential, exceptional_reduction
        FROM round WHERE golfer_id = ?
        ORDER BY played_on ASC, id ASC
    ''', (golfer_id,)).fetchall()
    all_rounds = [dict(r) for r in all_rounds]

    if not all_rounds:
        conn.close()
        flash('No rounds to recalculate.', 'info')
        return redirect(url_for('dashboard', golfer_id=golfer_id))

    conn.execute('DELETE FROM handicap_snapshot WHERE golfer_id = ?', (golfer_id,))

    count = 0
    for i, rnd in enumerate(all_rounds):
        rounds_so_far = all_rounds[:i + 1]
        handicap, rounds_used = calculate_handicap_as_of(rounds_so_far)
        if handicap is not None:
            conn.execute('''
                INSERT INTO handicap_snapshot (golfer_id, round_id, calculated_on, handicap_index, rounds_used)
                VALUES (?, ?, ?, ?, ?)
            ''', (golfer_id, rnd['id'], rnd['played_on'], handicap, rounds_used))
            count += 1

    conn.commit()
    conn.close()
    flash(f'Recalculated {count} snapshots across {len(all_rounds)} rounds.', 'success')
    conn.close()
    return redirect(url_for('dashboard', golfer_id=golfer_id))

@app.route('/golfer/<int:golfer_id>/round/add', methods=['POST'])
def add_round(golfer_id):
    blocked = require_golfer_access(golfer_id)
    if blocked:
        return blocked
    tee_set_id = request.form['tee_set_id']
    played_on = request.form['played_on']
    score = request.form.get('adjusted_gross_score', '').strip()
    notes = request.form.get('weather_notes', '').strip() or None
    holes_played = int(request.form.get('holes_played', '18'))
    nine = request.form.get('nine') or None
    if holes_played == 18:
        nine = None

    score = int(score) if score else 0

    conn = get_db()
    cursor = conn.execute('''
        INSERT INTO round (golfer_id, tee_set_id, played_on, holes_played, nine,
                           adjusted_gross_score, actual_gross_score, weather_notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (golfer_id, tee_set_id, played_on, holes_played, nine, score, score, notes))
    round_id = cursor.lastrowid
    conn.commit()

    # For 9-hole rounds, override the trigger-calculated differential
    if holes_played == 9 and score > 0:
        tee = dict(conn.execute('SELECT * FROM tee_set WHERE id = ?', (tee_set_id,)).fetchone())
        rating, slope, nine_par = get_nine_hole_rating(tee, nine)
        nine_diff = round((score - rating) * 113.0 / slope, 1)
        # Add expected differential for the unplayed nine (handicap index / 2)
        handicap, _, _ = calculate_handicap(golfer_id)
        expected_diff = handicap / 2.0 if handicap else 27.0
        full_diff = round(nine_diff + expected_diff, 1)
        conn.execute('UPDATE round SET score_differential = ? WHERE id = ?', (full_diff, round_id))
        conn.commit()

    # Auto-snapshot after the round differential is set
    save_snapshot(conn, golfer_id, round_id, played_on)
    check_exceptional_score(conn, golfer_id, round_id)
    conn.commit()
    conn.close()
    flash('Round saved. Click "Hole Scores" to enter your strokes.', 'success')
    return redirect(url_for('dashboard', golfer_id=golfer_id))


@app.route('/golfer/<int:golfer_id>/round/<int:round_id>/holes', methods=['POST'])
def save_hole_scores(golfer_id, round_id):
    blocked = require_golfer_access(golfer_id)
    if blocked:
        return blocked
    conn = get_db()

    # Get round and tee info
    round_info = conn.execute('''
        SELECT r.adjusted_gross_score, r.holes_played, r.nine,
               t.par, t.slope_rating, t.course_rating, t.hole_handicaps, t.id as tee_set_id,
               t.front_rating, t.front_slope, t.back_rating, t.back_slope
        FROM round r
        JOIN tee_set t ON r.tee_set_id = t.id
        WHERE r.id = ?
    ''', (round_id,)).fetchone()

    if not round_info:
        conn.close()
        flash('Round not found.', 'danger')
        return redirect(url_for('dashboard', golfer_id=golfer_id))

    round_info = dict(round_info)
    total_par = round_info['par']
    slope = round_info['slope_rating']
    course_rating = round_info['course_rating']
    hole_handicaps_str = round_info['hole_handicaps']
    tee_set_id = round_info['tee_set_id']
    holes_played = round_info['holes_played'] or 18
    nine = round_info['nine']

    # Determine hole range
    if holes_played == 9:
        hole_range = range(1, 10) if nine == 'front' else range(10, 19)
    else:
        hole_range = range(1, 19)

    # Get player's current handicap index (defaults to 54.0 for new golfers)
    handicap, rounds_used, _ = calculate_handicap(golfer_id)
    course_handicap = round((handicap * slope / 113) + (course_rating - total_par), 0)

    # Prefer the detailed hole table (par + handicap per hole) if it's been filled in
    hole_table = conn.execute('SELECT hole_number, par, handicap FROM hole WHERE tee_set_id = ?', (tee_set_id,)).fetchall()
    hole_par_map = {h['hole_number']: h['par'] for h in hole_table}
    hole_hcp_map = {h['hole_number']: h['handicap'] for h in hole_table}

    # Fall back to the comma-separated hole_handicaps string, then to a flat 1-18 ranking
    if not hole_hcp_map:
        hole_handicaps = []
        if hole_handicaps_str:
            try:
                hole_handicaps = [int(x.strip()) for x in hole_handicaps_str.split(',')]
            except:
                hole_handicaps = list(range(1, 19))
        else:
            hole_handicaps = list(range(1, 19))
        hole_hcp_map = {i + 1: hole_handicaps[i] for i in range(min(18, len(hole_handicaps)))}

    conn.execute('DELETE FROM hole_score WHERE round_id = ?', (round_id,))

    default_par_per_hole = total_par // 18
    actual_total = 0
    adjusted_total = 0
    for hole_num in hole_range:
        strokes_key = f'hole_{hole_num}_strokes'
        raw = request.form.get(strokes_key, '').strip()

        if raw:
            try:
                actual = int(raw)
                par_per_hole = hole_par_map.get(hole_num, default_par_per_hole)
                # ESC: net double bogey limit
                hole_hcp = hole_hcp_map.get(hole_num, hole_num)
                gets_stroke = hole_hcp <= course_handicap
                esc_limit = par_per_hole + 2 + (1 if gets_stroke else 0)
                adjusted = min(actual, esc_limit)

                actual_total += actual
                adjusted_total += adjusted

                conn.execute('''
                    INSERT INTO hole_score (round_id, hole_number, par, strokes, adjusted_strokes)
                    VALUES (?, ?, ?, ?, ?)
                ''', (round_id, hole_num, par_per_hole, actual, adjusted))
            except ValueError:
                pass

    # actual_gross_score = sum of real strokes; adjusted_gross_score = sum of ESC-capped strokes
    conn.execute('UPDATE round SET actual_gross_score = ?, adjusted_gross_score = ? WHERE id = ?',
                 (actual_total, adjusted_total, round_id))

    # Calculate differential — different for 9 vs 18 hole rounds
    if holes_played == 9:
        nine_rating, nine_slope, nine_par = get_nine_hole_rating(round_info, nine)
        nine_diff = round((adjusted_total - nine_rating) * 113.0 / nine_slope, 1)
        expected_diff = handicap / 2.0 if handicap else 27.0
        full_diff = round(nine_diff + expected_diff, 1)
        conn.execute('UPDATE round SET score_differential = ? WHERE id = ?', (full_diff, round_id))
    else:
        conn.execute('''
            UPDATE round SET score_differential = ROUND((adjusted_gross_score - ?) * 113.0 / ?, 1)
            WHERE id = ?
        ''', (course_rating, slope, round_id))
    
    conn.commit()

    # Update the snapshot tied to this round with the new differential
    played_on = conn.execute('SELECT played_on FROM round WHERE id = ?', (round_id,)).fetchone()['played_on']
    save_snapshot(conn, golfer_id, round_id, played_on)
    check_exceptional_score(conn, golfer_id, round_id)
    conn.commit()

    conn.close()
    if actual_total != adjusted_total:
        flash(f'Hole scores saved. Actual: {actual_total}, Adjusted: {adjusted_total} (ESC applied).', 'success')
    else:
        flash(f'Hole scores saved. Total score: {actual_total}.', 'success')
    return redirect(url_for('dashboard', golfer_id=golfer_id))

@app.route('/golfer/<int:golfer_id>/round/<int:round_id>/edit', methods=['POST'])
def edit_round(golfer_id, round_id):
    blocked = require_golfer_access(golfer_id)
    if blocked:
        return blocked

    tee_set_id = request.form.get('tee_set_id')
    played_on = request.form.get('played_on')
    score = request.form.get('adjusted_gross_score', '').strip()
    notes = request.form.get('weather_notes', '').strip() or None
    holes_played = int(request.form.get('holes_played', '18'))
    nine = request.form.get('nine') or None
    if holes_played == 18:
        nine = None

    score = int(score) if score else 0

    conn = get_db()
    conn.execute('''
        UPDATE round SET tee_set_id = ?, played_on = ?, holes_played = ?, nine = ?,
                         adjusted_gross_score = ?, actual_gross_score = ?, weather_notes = ?
        WHERE id = ? AND golfer_id = ?
    ''', (tee_set_id, played_on, holes_played, nine, score, score, notes, round_id, golfer_id))

    # Recalculate differential
    tee = dict(conn.execute('SELECT * FROM tee_set WHERE id = ?', (tee_set_id,)).fetchone())
    if holes_played == 9 and score > 0:
        rating, slope, nine_par = get_nine_hole_rating(tee, nine)
        nine_diff = round((score - rating) * 113.0 / slope, 1)
        handicap, _, _ = calculate_handicap(golfer_id)
        expected_diff = handicap / 2.0 if handicap else 27.0
        full_diff = round(nine_diff + expected_diff, 1)
        conn.execute('UPDATE round SET score_differential = ? WHERE id = ?', (full_diff, round_id))
    elif score > 0:
        diff = round((score - tee['course_rating']) * 113.0 / tee['slope_rating'], 1)
        conn.execute('UPDATE round SET score_differential = ? WHERE id = ?', (diff, round_id))

    conn.commit()
    save_snapshot(conn, golfer_id, round_id, played_on)
    conn.commit()
    conn.close()
    flash('Round updated.', 'success')
    return redirect(url_for('dashboard', golfer_id=golfer_id))


@app.route('/golfer/<int:golfer_id>/round/<int:round_id>/delete', methods=['POST'])
def delete_round(golfer_id, round_id):
    blocked = require_golfer_access(golfer_id)
    if blocked:
        return blocked
    conn = get_db()
    conn.execute('DELETE FROM handicap_snapshot WHERE round_id = ?', (round_id,))
    conn.execute('DELETE FROM hole_score WHERE round_id = ?', (round_id,))
    conn.execute('DELETE FROM round WHERE id = ? AND golfer_id = ?', (round_id, golfer_id))
    conn.commit()
    conn.close()
    flash('Round deleted.', 'info')
    return redirect(url_for('dashboard', golfer_id=golfer_id))

@app.route('/courses')
def courses():
    conn = get_db()
    courses_list = conn.execute('''
        SELECT c.*, COUNT(t.id) as tee_count
        FROM course c LEFT JOIN tee_set t ON c.id = t.course_id
        GROUP BY c.id ORDER BY c.name
    ''').fetchall()
    courses_data = [dict(c) for c in courses_list]

    selected = None
    course_id = request.args.get('course_id', type=int)
    if course_id:
        for c in courses_data:
            if c['id'] == course_id:
                tees = conn.execute('SELECT * FROM tee_set WHERE course_id = ? ORDER BY course_rating DESC', (course_id,)).fetchall()
                tees = [dict(t) for t in tees]
                for t in tees:
                    holes = conn.execute(
                        'SELECT hole_number, par, handicap FROM hole WHERE tee_set_id = ? ORDER BY hole_number',
                        (t['id'],)
                    ).fetchall()
                    t['holes'] = [dict(h) for h in holes]
                c['tees'] = tees
                selected = c
                break

    conn.close()
    show_add = request.args.get('add') == '1'
    course_json = json.dumps([
        {'id': c['id'], 'text': c['name'] + (' · ' + c['city'] + (', ' + c['country'] if c.get('country') else '') if c.get('city') else '')}
        for c in courses_data
    ])
    return render_template('courses.html', courses=courses_data, selected=selected, show_add=show_add, course_json=course_json)

@app.route('/course/<int:course_id>/detail')
def course_detail(course_id):
    conn = get_db()
    course = conn.execute('SELECT * FROM course WHERE id = ?', (course_id,)).fetchone()
    if not course:
        conn.close()
        flash('Course not found.', 'danger')
        return redirect(url_for('courses'))
    course = dict(course)

    tees = conn.execute('SELECT * FROM tee_set WHERE course_id = ? ORDER BY course_rating DESC', (course_id,)).fetchall()
    tees = [dict(t) for t in tees]

    for tee in tees:
        holes = conn.execute(
            'SELECT hole_number, par, handicap FROM hole WHERE tee_set_id = ? ORDER BY hole_number',
            (tee['id'],)
        ).fetchall()
        tee['holes'] = [dict(h) for h in holes]

    conn.close()
    return render_template('course_detail.html', course=course, tees=tees)


@app.route('/course/add', methods=['POST'])
def add_course():
    blocked = require_admin()
    if blocked:
        return blocked
    name = request.form['name'].strip()
    city = request.form.get('city', '').strip() or None
    country = request.form.get('country', '').strip() or None
    if name:
        conn = get_db()
        conn.execute('INSERT INTO course (name, city, country) VALUES (?, ?, ?)', (name, city, country))
        conn.commit()
        conn.close()
        flash(f'Course "{name}" added.', 'success')
    return redirect(url_for('courses'))

@app.route('/course/add-tee', methods=['POST'])
def add_tee():
    blocked = require_admin()
    if blocked:
        return blocked
    course_id = request.form.get('course_id', '').strip()
    if not course_id:
        flash('Please select a course.', 'danger')
        return redirect(url_for('courses'))
    
    try:
        course_id = int(course_id)
    except ValueError:
        flash('Invalid course selected.', 'danger')
        return redirect(url_for('courses'))
    
    conn = get_db()
    conn.execute('''
        INSERT INTO tee_set (course_id, tee_name, gender, course_rating, slope_rating, par)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (course_id,
          request.form['tee_name'],
          request.form['gender'],
          request.form['course_rating'],
          request.form['slope_rating'],
          request.form['par']))
    conn.commit()
    conn.close()
    flash('Tee set added.', 'success')
    return redirect(url_for('courses'))

@app.route('/course/<int:course_id>/edit', methods=['POST'])
def edit_course(course_id):
    blocked = require_admin()
    if blocked:
        return blocked
    name = request.form.get('name', '').strip()
    city = request.form.get('city', '').strip() or None
    country = request.form.get('country', '').strip() or None
    if name:
        conn = get_db()
        conn.execute('UPDATE course SET name = ?, city = ?, country = ? WHERE id = ?',
                     (name, city, country, course_id))
        conn.commit()
        conn.close()
        flash(f'Course updated.', 'success')
    return redirect(url_for('courses', course_id=course_id))


@app.route('/tee/<int:tee_id>/edit', methods=['POST'])
def edit_tee(tee_id):
    blocked = require_admin()
    if blocked:
        return blocked
    conn = get_db()
    tee = conn.execute('SELECT course_id FROM tee_set WHERE id = ?', (tee_id,)).fetchone()
    if not tee:
        conn.close()
        flash('Tee set not found.', 'danger')
        return redirect(url_for('courses'))

    tee_name = request.form.get('tee_name', '').strip()
    gender = request.form.get('gender', 'Any').strip()
    if gender not in ('M', 'F', 'Any'):
        gender = 'Any'

    try:
        cr = float(request.form['course_rating'])
        sr = int(float(request.form['slope_rating']))
        par = int(float(request.form['par']))
    except (ValueError, KeyError):
        conn.close()
        flash('Invalid tee data.', 'danger')
        return redirect(url_for('courses', course_id=tee['course_id']))

    conn.execute('''
        UPDATE tee_set SET tee_name = ?, gender = ?, course_rating = ?, slope_rating = ?, par = ?
        WHERE id = ?
    ''', (tee_name, gender, cr, sr, par, tee_id))
    conn.commit()
    conn.close()
    flash('Tee set updated.', 'success')
    return redirect(url_for('courses', course_id=tee['course_id']))


@app.route('/tee/<int:tee_id>/copy', methods=['POST'])
def copy_tee(tee_id):
    blocked = require_admin()
    if blocked:
        return blocked
    conn = get_db()
    original = conn.execute('SELECT * FROM tee_set WHERE id = ?', (tee_id,)).fetchone()
    
    if not original:
        conn.close()
        flash('Tee set not found.', 'danger')
        return redirect(url_for('courses'))
    
    original = dict(original)
    new_name = f"{original['tee_name']} (Copy)"
    
    cursor = conn.execute('''
        INSERT INTO tee_set (course_id, tee_name, gender, course_rating, slope_rating, par, hole_handicaps)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (original['course_id'], new_name, original['gender'],
          original['course_rating'], original['slope_rating'], original['par'],
          original.get('hole_handicaps')))
    
    new_tee_id = cursor.lastrowid
    
    # Copy hole-by-hole par/handicap data too
    holes = conn.execute('SELECT * FROM hole WHERE tee_set_id = ? ORDER BY hole_number', (tee_id,)).fetchall()
    for h in holes:
        conn.execute('''
            INSERT INTO hole (tee_set_id, hole_number, par, handicap)
            VALUES (?, ?, ?, ?)
        ''', (new_tee_id, h['hole_number'], h['par'], h['handicap']))
    
    conn.commit()
    conn.close()
    flash(f'Copied to "{new_name}". Edit the rating, slope, name, or holes as needed.', 'success')
    return redirect(url_for('edit_holes', tee_id=new_tee_id))

@app.route('/course/<int:course_id>/delete', methods=['POST'])
def delete_course(course_id):
    blocked = require_admin()
    if blocked:
        return blocked
    conn = get_db()
    try:
        tee_ids = [t['id'] for t in conn.execute(
            'SELECT id FROM tee_set WHERE course_id = ?', (course_id,)
        ).fetchall()]

        for tid in tee_ids:
            conn.execute('DELETE FROM hole WHERE tee_set_id = ?', (tid,))
            round_ids = [r['id'] for r in conn.execute(
                'SELECT id FROM round WHERE tee_set_id = ?', (tid,)
            ).fetchall()]
            for rid in round_ids:
                conn.execute('DELETE FROM handicap_snapshot WHERE round_id = ?', (rid,))
                conn.execute('DELETE FROM hole_score WHERE round_id = ?', (rid,))
            conn.execute('DELETE FROM round WHERE tee_set_id = ?', (tid,))

        conn.execute('DELETE FROM tee_set WHERE course_id = ?', (course_id,))
        conn.execute('DELETE FROM course WHERE id = ?', (course_id,))
        conn.commit()
        flash('Course deleted.', 'info')
    finally:
        conn.close()
    return redirect(url_for('courses'))

@app.route('/tee/<int:tee_id>/delete', methods=['POST'])
def delete_tee(tee_id):
    blocked = require_admin()
    if blocked:
        return blocked
    conn = get_db()
    try:
        result = conn.execute('SELECT course_id FROM tee_set WHERE id = ?', (tee_id,)).fetchone()
        if result:
            # Delete hole-by-hole detail rows for this tee set
            conn.execute('DELETE FROM hole WHERE tee_set_id = ?', (tee_id,))

            # Find any rounds played on this tee set, and clean up their hole_score rows first
            round_ids = [r['id'] for r in conn.execute(
                'SELECT id FROM round WHERE tee_set_id = ?', (tee_id,)
            ).fetchall()]
            for rid in round_ids:
                conn.execute('DELETE FROM handicap_snapshot WHERE round_id = ?', (rid,))
                conn.execute('DELETE FROM hole_score WHERE round_id = ?', (rid,))
            conn.execute('DELETE FROM round WHERE tee_set_id = ?', (tee_id,))

            # Now it's safe to delete the tee set itself
            conn.execute('DELETE FROM tee_set WHERE id = ?', (tee_id,))
            conn.commit()
            if round_ids:
                flash(f'Tee set deleted, along with {len(round_ids)} round(s) recorded on it.', 'info')
            else:
                flash('Tee set deleted.', 'info')
        else:
            flash('Tee set not found.', 'danger')
    finally:
        conn.close()
    return redirect(url_for('courses'))

@app.route('/tee/<int:tee_id>/holes')
def edit_holes(tee_id):
    conn = get_db()
    tee = conn.execute('SELECT t.*, c.name as course_name FROM tee_set t JOIN course c ON t.course_id = c.id WHERE t.id = ?', (tee_id,)).fetchone()
    
    if not tee:
        conn.close()
        flash('Tee set not found.', 'danger')
        return redirect(url_for('courses'))
    
    holes = conn.execute('SELECT * FROM hole WHERE tee_set_id = ? ORDER BY hole_number', (tee_id,)).fetchall()
    holes = [dict(h) for h in holes]
    conn.close()
    
    tee_dict = dict(tee)
    return render_template('holes.html', tee=tee_dict, holes=holes)

@app.route('/tee/<int:tee_id>/holes/save', methods=['POST'])
def save_holes(tee_id):
    blocked = require_admin()
    if blocked:
        return blocked
    conn = get_db()
    conn.execute('DELETE FROM hole WHERE tee_set_id = ?', (tee_id,))
    
    total_par = 0
    for hole_num in range(1, 19):
        par_key = f'hole_{hole_num}_par'
        hcp_key = f'hole_{hole_num}_handicap'
        
        par = request.form.get(par_key, '').strip()
        handicap = request.form.get(hcp_key, '').strip()
        
        if par and handicap:
            try:
                par = int(par)
                handicap = int(handicap)
                
                if 3 <= par <= 5 and 1 <= handicap <= 18:
                    conn.execute('''
                        INSERT INTO hole (tee_set_id, hole_number, par, handicap)
                        VALUES (?, ?, ?, ?)
                    ''', (tee_id, hole_num, par, handicap))
                    total_par += par
            except ValueError:
                pass
    
    # Save tee set details (name, gender, rating, slope). Par comes from the
    # hole grid if any holes were entered, otherwise keep the existing value.
    tee_name = request.form.get('tee_name', '').strip()
    gender = request.form.get('gender', '').strip()
    course_rating = request.form.get('course_rating', '').strip()
    slope_rating = request.form.get('slope_rating', '').strip()
    
    # 9-hole ratings (optional)
    front_rating = request.form.get('front_rating', '').strip() or None
    front_slope = request.form.get('front_slope', '').strip() or None
    back_rating = request.form.get('back_rating', '').strip() or None
    back_slope = request.form.get('back_slope', '').strip() or None

    if tee_name and gender and course_rating and slope_rating:
        if total_par > 0:
            conn.execute('''
                UPDATE tee_set
                SET tee_name = ?, gender = ?, course_rating = ?, slope_rating = ?, par = ?,
                    front_rating = ?, front_slope = ?, back_rating = ?, back_slope = ?
                WHERE id = ?
            ''', (tee_name, gender, course_rating, slope_rating, total_par,
                  front_rating, front_slope, back_rating, back_slope, tee_id))
        else:
            conn.execute('''
                UPDATE tee_set
                SET tee_name = ?, gender = ?, course_rating = ?, slope_rating = ?,
                    front_rating = ?, front_slope = ?, back_rating = ?, back_slope = ?
                WHERE id = ?
            ''', (tee_name, gender, course_rating, slope_rating,
                  front_rating, front_slope, back_rating, back_slope, tee_id))
    
    conn.commit()
    conn.close()
    flash('Tee set saved.', 'success')
    return redirect(url_for('courses'))

@app.route('/api/tees/<int:course_id>')
def api_tees(course_id):
    conn = get_db()
    tees = conn.execute('SELECT * FROM tee_set WHERE course_id = ? ORDER BY course_rating DESC', (course_id,)).fetchall()
    conn.close()
    return jsonify([dict(t) for t in tees])

@app.route('/tee/<int:tee_id>/scorecard')
def scorecard(tee_id):
    conn = get_db()
    tee = conn.execute('''
        SELECT t.*, c.name as course_name, c.city, c.country
        FROM tee_set t JOIN course c ON t.course_id = c.id
        WHERE t.id = ?
    ''', (tee_id,)).fetchone()

    if not tee:
        conn.close()
        flash('Tee set not found.', 'danger')
        return redirect(url_for('courses'))

    tee = dict(tee)

    holes = conn.execute(
        'SELECT * FROM hole WHERE tee_set_id = ? ORDER BY hole_number',
        (tee_id,)
    ).fetchall()
    holes = [dict(h) for h in holes]

    golfers = conn.execute('SELECT * FROM golfer ORDER BY name').fetchall()
    golfers = [dict(g) for g in golfers]
    conn.close()

    # Build a full 18-hole list, filling gaps with None
    hole_map = {h['hole_number']: h for h in holes}
    hole_list = [hole_map.get(n) for n in range(1, 19)]

    # Optional golfer
    golfer_id = request.args.get('golfer_id', type=int)
    player = None
    course_handicap = None
    strokes_per_hole = {}

    if golfer_id:
        player = next((g for g in golfers if g['id'] == golfer_id), None)
        if player:
            handicap, _, _ = calculate_handicap(golfer_id)
            if handicap is not None:
                raw_ch = handicap * tee['slope_rating'] / 113.0 + (tee['course_rating'] - tee['par'])
                course_handicap = int(round(raw_ch))
                for h in holes:
                    n = h['hole_number']
                    hcp = h['handicap']
                    if course_handicap >= 18:
                        strokes = 1 + (1 if hcp <= (course_handicap - 18) else 0)
                    elif course_handicap > 0:
                        strokes = 1 if hcp <= course_handicap else 0
                    else:
                        strokes = 0
                    strokes_per_hole[n] = strokes

    # Par totals
    front_par = sum(h['par'] for h in holes if h and h['hole_number'] <= 9)
    back_par  = sum(h['par'] for h in holes if h and h['hole_number'] >= 10)

    return render_template('scorecard.html',
        tee=tee,
        hole_list=hole_list,
        golfers=golfers,
        player=player,
        course_handicap=course_handicap,
        strokes_per_hole=strokes_per_hole,
        front_par=front_par,
        back_par=back_par,
        selected_golfer_id=golfer_id,
    )

@app.route('/golfer/<int:golfer_id>/report')
def player_report(golfer_id):
    blocked = require_golfer_access(golfer_id)
    if blocked:
        return blocked
    conn = get_db()
    golfer = conn.execute('SELECT * FROM golfer WHERE id = ?', (golfer_id,)).fetchone()
    golfer = dict(golfer) if golfer else None

    rounds_data = conn.execute('''
        SELECT r.*, c.name as course_name, t.tee_name, t.par
        FROM round r
        JOIN tee_set t ON r.tee_set_id = t.id
        JOIN course c ON t.course_id = c.id
        WHERE r.golfer_id = ?
        ORDER BY r.played_on DESC
        LIMIT 20
    ''', (golfer_id,)).fetchall()
    rounds = [dict(r) for r in rounds_data]

    low_row = conn.execute('''
        SELECT MIN(handicap_index) as low_index
        FROM handicap_snapshot
        WHERE golfer_id = ? AND calculated_on >= date('now', '-1 year')
    ''', (golfer_id,)).fetchone()
    low_index = low_row['low_index'] if low_row else None
    conn.close()

    handicap, rounds_used, used_round_ids = calculate_handicap(golfer_id)

    capped_handicap = handicap
    if handicap is not None and low_index is not None:
        diff = handicap - low_index
        if diff > 5.0:
            capped_handicap = round(low_index + 5.0, 1)
        elif diff > 3.0:
            capped_handicap = round(low_index + 3.0 + (diff - 3.0) * 0.5, 1)

    by_date = list(rounds)
    by_diff = sorted([r for r in rounds if r['score_differential'] is not None],
                     key=lambda r: r['score_differential'])

    return render_template('report.html',
        golfer=golfer,
        handicap=capped_handicap,
        uncapped_handicap=handicap,
        low_index=low_index,
        rounds_used=rounds_used,
        used_round_ids=used_round_ids,
        by_date=by_date,
        by_diff=by_diff,
        today=date.today().isoformat(),
    )

@app.route('/courses/export')
def export_courses():
    blocked = require_admin()
    if blocked:
        return blocked
    conn = get_db()
    rows = conn.execute('''
        SELECT c.name as course_name, c.city, c.country,
               t.id as tee_id, t.tee_name, t.gender, t.course_rating, t.slope_rating, t.par,
               t.front_rating, t.front_slope, t.back_rating, t.back_slope
        FROM course c
        JOIN tee_set t ON t.course_id = c.id
        ORDER BY c.name, t.tee_name
    ''').fetchall()

    tee_ids = [r['tee_id'] for r in rows]
    hole_data = {}
    if tee_ids:
        placeholders = ','.join('?' * len(tee_ids))
        holes = conn.execute(
            f'SELECT tee_set_id, hole_number, par, handicap FROM hole WHERE tee_set_id IN ({placeholders}) ORDER BY hole_number',
            tee_ids
        ).fetchall()
        for h in holes:
            hole_data.setdefault(h['tee_set_id'], {})[h['hole_number']] = h
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['course_name', 'city', 'country', 'tee_name', 'gender',
                     'course_rating', 'slope_rating', 'par',
                     'front_rating', 'front_slope', 'back_rating', 'back_slope',
                     'hole_pars', 'hole_handicaps'])
    for r in rows:
        hd = hole_data.get(r['tee_id'], {})
        if hd:
            hole_pars = ','.join(str(hd[n]['par']) if n in hd else '' for n in range(1, 19))
            hole_hcps = ','.join(str(hd[n]['handicap']) if n in hd else '' for n in range(1, 19))
        else:
            hole_pars = ''
            hole_hcps = ''
        writer.writerow([r['course_name'], r['city'] or '', r['country'] or '',
                         r['tee_name'], r['gender'],
                         r['course_rating'], r['slope_rating'], r['par'],
                         r['front_rating'] or '', r['front_slope'] or '',
                         r['back_rating'] or '', r['back_slope'] or '',
                         hole_pars, hole_hcps])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=courses_export.csv'}
    )


@app.route('/courses/import', methods=['POST'])
def import_courses():
    blocked = require_admin()
    if blocked:
        return blocked

    file = request.files.get('file')
    if not file or not file.filename:
        flash('No file selected.', 'danger')
        return redirect(url_for('courses'))

    try:
        text = file.stream.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text))
    except Exception:
        flash('Could not read CSV file.', 'danger')
        return redirect(url_for('courses'))

    required = {'course_name', 'tee_name', 'course_rating', 'slope_rating', 'par'}
    if not required.issubset(set(reader.fieldnames or [])):
        flash(f'CSV must have columns: {", ".join(sorted(required))}', 'danger')
        return redirect(url_for('courses'))

    conn = get_db()
    added_courses = 0
    added_tees = 0
    skipped_tees = 0

    for row in reader:
        course_name = (row.get('course_name') or '').strip()
        tee_name = (row.get('tee_name') or '').strip()
        if not course_name or not tee_name:
            continue

        city = (row.get('city') or '').strip() or None
        country = (row.get('country') or '').strip() or None

        course = conn.execute(
            'SELECT id FROM course WHERE TRIM(LOWER(name)) = TRIM(LOWER(?))', (course_name,)
        ).fetchone()

        if course:
            course_id = course['id']
        else:
            cursor = conn.execute(
                'INSERT INTO course (name, city, country) VALUES (?, ?, ?)',
                (course_name, city, country)
            )
            course_id = cursor.lastrowid
            added_courses += 1

        existing = conn.execute(
            'SELECT id FROM tee_set WHERE course_id = ? AND TRIM(LOWER(tee_name)) = TRIM(LOWER(?))',
            (course_id, tee_name)
        ).fetchone()
        if existing:
            skipped_tees += 1
            continue

        gender = (row.get('gender') or 'Any').strip()
        if gender not in ('M', 'F', 'Any'):
            gender = 'Any'

        try:
            cr = float(row['course_rating'])
            sr = int(float(row['slope_rating']))
            par = int(float(row['par']))
        except (ValueError, KeyError):
            continue

        fr = float(row['front_rating']) if row.get('front_rating', '').strip() else None
        fs = int(float(row['front_slope'])) if row.get('front_slope', '').strip() else None
        br = float(row['back_rating']) if row.get('back_rating', '').strip() else None
        bs = int(float(row['back_slope'])) if row.get('back_slope', '').strip() else None

        cursor = conn.execute('''
            INSERT INTO tee_set (course_id, tee_name, gender, course_rating, slope_rating, par,
                                 front_rating, front_slope, back_rating, back_slope)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (course_id, tee_name, gender, cr, sr, par, fr, fs, br, bs))
        new_tee_id = cursor.lastrowid

        hole_pars_str = (row.get('hole_pars') or '').strip()
        hole_hcps_str = (row.get('hole_handicaps') or '').strip()
        if hole_pars_str and hole_hcps_str:
            try:
                pars = [int(x) for x in hole_pars_str.split(',') if x.strip()]
                hcps = [int(x) for x in hole_hcps_str.split(',') if x.strip()]
                for i, (p, h) in enumerate(zip(pars, hcps), start=1):
                    conn.execute(
                        'INSERT INTO hole (tee_set_id, hole_number, par, handicap) VALUES (?, ?, ?, ?)',
                        (new_tee_id, i, p, h)
                    )
            except (ValueError, IndexError):
                pass

        added_tees += 1

    conn.commit()
    conn.close()

    parts = []
    if added_courses:
        parts.append(f'{added_courses} course{"s" if added_courses != 1 else ""}')
    if added_tees:
        parts.append(f'{added_tees} tee set{"s" if added_tees != 1 else ""}')
    if skipped_tees:
        parts.append(f'{skipped_tees} duplicate{"s" if skipped_tees != 1 else ""} skipped')

    flash(f'Import complete: {", ".join(parts) or "nothing new to import"}.', 'success')
    return redirect(url_for('courses'))


_db_ready = False

@app.before_request
def ensure_db():
    global _db_ready
    if not _db_ready:
        init_db()
        migrate_db()
        _db_ready = True

if __name__ == '__main__':
    init_db()
    migrate_db()
    print("\n⛳  Golf Handicap Tracker running at http://localhost:5000\n")
    app.run(debug=False, port=5000)