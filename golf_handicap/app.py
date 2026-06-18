import sqlite3
import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from datetime import date

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-in-prod')

DB_PATH = os.path.expanduser('~/golf_handicap.db')

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
            hole_handicaps TEXT
        );
        CREATE TABLE IF NOT EXISTS round (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            golfer_id INTEGER REFERENCES golfer(id),
            tee_set_id INTEGER REFERENCES tee_set(id),
            played_on DATE NOT NULL,
            adjusted_gross_score INTEGER NOT NULL,
            score_differential REAL,
            weather_notes TEXT
        );
        CREATE TABLE IF NOT EXISTS hole_score (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER REFERENCES round(id),
            hole_number INTEGER CHECK (hole_number BETWEEN 1 AND 18),
            par INTEGER,
            strokes INTEGER NOT NULL,
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
            calculated_on DATE NOT NULL,
            handicap_index REAL NOT NULL,
            rounds_used INTEGER NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS calc_differential
        AFTER INSERT ON round
        BEGIN
            UPDATE round
            SET score_differential = (
                NEW.adjusted_gross_score - (SELECT course_rating FROM tee_set WHERE id = NEW.tee_set_id)
            ) * 113.0 / (SELECT slope_rating FROM tee_set WHERE id = NEW.tee_set_id)
            WHERE id = NEW.id;
        END;
    ''')
    conn.commit()
    conn.close()

def calculate_handicap(golfer_id):
    conn = get_db()
    rows = conn.execute('''
        SELECT id, score_differential FROM round
        WHERE golfer_id = ?
        ORDER BY played_on DESC LIMIT 20
    ''', (golfer_id,)).fetchall()
    conn.close()

    valid = [(r['id'], r['score_differential']) for r in rows if r['score_differential'] is not None]
    n = len(valid)
    if n < 3:
        return None, n, set()

    # WHS sliding scale
    use = {3:1, 4:1, 5:1, 6:2, 7:2, 8:2, 9:3, 10:3, 11:3, 12:4, 13:4, 14:4,
           15:5, 16:5, 17:6, 18:6, 19:7}.get(n, 8)

    # Sort by differential ascending; take the best `use` rounds
    sorted_valid = sorted(valid, key=lambda x: x[1])
    used_ids = {row[0] for row in sorted_valid[:use]}

    avg = sum(row[1] for row in sorted_valid[:use]) / use
    return round(avg * 0.96, 1), n, used_ids

# ── Routes ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    conn = get_db()
    golfers = conn.execute('SELECT * FROM golfer ORDER BY name').fetchall()
    conn.close()
    return render_template('index.html', golfers=golfers)

@app.route('/golfer/add', methods=['POST'])
def add_golfer():
    name = request.form['name'].strip()
    email = request.form.get('email', '').strip() or None
    if name:
        conn = get_db()
        conn.execute('INSERT INTO golfer (name, email) VALUES (?, ?)', (name, email))
        conn.commit()
        conn.close()
        flash(f'Golfer "{name}" added.', 'success')
    return redirect(url_for('index'))

@app.route('/golfer/<int:golfer_id>')
def dashboard(golfer_id):
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
        WHERE r.golfer_id = ?
        ORDER BY r.played_on DESC
        LIMIT 20
    ''', (golfer_id,)).fetchall()
    
    rounds = []
    for r in rounds_data:
        round_dict = dict(r)
        
        # === THIS IS THE IMPORTANT PART ===
        hole_scores_data = conn.execute('''
            SELECT hole_number, strokes 
            FROM hole_score 
            WHERE round_id = ?
        ''', (round_dict['id'],)).fetchall()
        
        round_dict['hole_scores'] = {row['hole_number']: row['strokes'] 
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
    
    snapshots_data = conn.execute('''
        SELECT calculated_on, handicap_index, rounds_used
        FROM handicap_snapshot
        WHERE golfer_id = ?
        ORDER BY calculated_on DESC, id DESC
        LIMIT 5
    ''', (golfer_id,)).fetchall()
    snapshots = [dict(s) for s in snapshots_data]
    
    conn.close()

    handicap, rounds_used, used_round_ids = calculate_handicap(golfer_id)

    return render_template('dashboard.html',
        golfer=golfer,
        rounds=rounds,
        courses=courses,
        handicap=handicap,
        rounds_used=rounds_used,
        used_round_ids=used_round_ids,
        snapshots=snapshots,
        today=date.today().isoformat()
    )
@app.route('/golfer/<int:golfer_id>/round/add', methods=['POST'])
def add_round(golfer_id):
    tee_set_id = request.form['tee_set_id']
    played_on = request.form['played_on']
    score = request.form.get('adjusted_gross_score', '').strip()
    notes = request.form.get('weather_notes', '').strip() or None

    score = int(score) if score else 0

    conn = get_db()
    conn.execute('''
        INSERT INTO round (golfer_id, tee_set_id, played_on, adjusted_gross_score, weather_notes)
        VALUES (?, ?, ?, ?, ?)
    ''', (golfer_id, tee_set_id, played_on, score, notes))
    conn.commit()
    conn.close()
    flash('Round saved. Click "Hole Scores" to enter your strokes.', 'success')
    return redirect(url_for('dashboard', golfer_id=golfer_id))

@app.route('/golfer/<int:golfer_id>/recalculate', methods=['POST'])
def recalculate_handicap(golfer_id):
    handicap, rounds_used, _ = calculate_handicap(golfer_id)
    
    conn = get_db()
    if handicap is not None:
        conn.execute('''
            INSERT INTO handicap_snapshot (golfer_id, calculated_on, handicap_index, rounds_used)
            VALUES (?, date('now'), ?, ?)
        ''', (golfer_id, handicap, rounds_used))
        conn.commit()
        flash(f'Handicap recalculated: {handicap} (from {rounds_used} rounds).', 'success')
    else:
        flash(f'Need at least 3 rounds to calculate a handicap (you have {rounds_used}).', 'info')
    conn.close()
    return redirect(url_for('dashboard', golfer_id=golfer_id))

@app.route('/golfer/<int:golfer_id>/round/<int:round_id>/holes', methods=['POST'])
def save_hole_scores(golfer_id, round_id):
    conn = get_db()
    
    # Get round and tee info
    round_info = conn.execute('''
        SELECT r.adjusted_gross_score, t.par, t.slope_rating, t.course_rating, t.hole_handicaps, t.id as tee_set_id
        FROM round r
        JOIN tee_set t ON r.tee_set_id = t.id
        WHERE r.id = ?
    ''', (round_id,)).fetchone()
    
    if not round_info:
        conn.close()
        flash('Round not found.', 'danger')
        return redirect(url_for('dashboard', golfer_id=golfer_id))
    
    total_par = round_info['par']
    slope = round_info['slope_rating']
    course_rating = round_info['course_rating']
    hole_handicaps_str = round_info['hole_handicaps']
    tee_set_id = round_info['tee_set_id']
    
    # Get player's current handicap index
    handicap, rounds_used, _ = calculate_handicap(golfer_id)
    
    # ESC only applies once a handicap index actually exists (3+ prior rounds).
    # Before that, course_handicap is undefined, so don't cap anything.
    apply_esc = handicap is not None
    
    if apply_esc:
        course_handicap = round((handicap * slope / 113) + (course_rating - total_par), 0)
    else:
        course_handicap = None
    
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
    total_strokes = 0
    for hole_num in range(1, 19):
        strokes_key = f'hole_{hole_num}_strokes'
        strokes = request.form.get(strokes_key, '').strip()
        
        if strokes:
            try:
                strokes = int(strokes)
                par_per_hole = hole_par_map.get(hole_num, default_par_per_hole)
                
                if apply_esc:
                    # Determine if player gets a stroke on this hole
                    hole_hcp = hole_hcp_map.get(hole_num, hole_num)
                    gets_stroke = hole_hcp <= course_handicap
                    
                    # ESC limit: Par + 2 (net double bogey) + strokes player gets on hole
                    esc_limit = par_per_hole + 2 + (1 if gets_stroke else 0)
                    
                    strokes = min(strokes, esc_limit)
                
                total_strokes += strokes
                
                conn.execute('''
                    INSERT INTO hole_score (round_id, hole_number, par, strokes)
                    VALUES (?, ?, ?, ?)
                ''', (round_id, hole_num, par_per_hole, strokes))
            except ValueError:
                pass
    
    # Recalculate total score and differential from hole scores
    conn.execute('UPDATE round SET adjusted_gross_score = ? WHERE id = ?', (total_strokes, round_id))
    conn.execute('''
        UPDATE round SET score_differential = (adjusted_gross_score - ?) * 113.0 / ?
        WHERE id = ?
    ''', (course_rating, slope, round_id))
    
    conn.commit()
    conn.close()
    if apply_esc:
        flash(f'Hole scores saved. Total score: {total_strokes} (ESC applied).', 'success')
    else:
        flash(f'Hole scores saved. Total score: {total_strokes} (raw score \u2014 ESC starts after 3 rounds).', 'success')
    return redirect(url_for('dashboard', golfer_id=golfer_id))

@app.route('/golfer/<int:golfer_id>/round/<int:round_id>/delete', methods=['POST'])
def delete_round(golfer_id, round_id):
    conn = get_db()
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
    
    # Get tee sets for each course
    courses_data = []
    for course in courses_list:
        tees = conn.execute('SELECT * FROM tee_set WHERE course_id = ? ORDER BY course_rating DESC', (course['id'],)).fetchall()
        course_dict = dict(course)
        course_dict['tees'] = [dict(t) for t in tees]
        courses_data.append(course_dict)
    
    conn.close()
    return render_template('courses.html', courses=courses_data)

@app.route('/course/add', methods=['POST'])
def add_course():
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

@app.route('/tee/<int:tee_id>/copy', methods=['POST'])
def copy_tee(tee_id):
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
    
    if tee_name and gender and course_rating and slope_rating:
        if total_par > 0:
            conn.execute('''
                UPDATE tee_set
                SET tee_name = ?, gender = ?, course_rating = ?, slope_rating = ?, par = ?
                WHERE id = ?
            ''', (tee_name, gender, course_rating, slope_rating, total_par, tee_id))
        else:
            conn.execute('''
                UPDATE tee_set
                SET tee_name = ?, gender = ?, course_rating = ?, slope_rating = ?
                WHERE id = ?
            ''', (tee_name, gender, course_rating, slope_rating, tee_id))
    
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

if __name__ == '__main__':
    init_db()
    print("\n⛳  Golf Handicap Tracker running at http://localhost:5000\n")
    app.run(debug=False, port=5000)
