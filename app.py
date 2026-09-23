from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import mysql.connector
from functools import wraps
import cv2
import numpy as np
import face_recognition
import os                                                 
import base64
from datetime import datetime, date
import json
from ml_predictor import analyse_student
from email_alerts import (
    ensure_alert_log_table,
    check_and_send_alert,
    send_bulk_alerts,
    send_test_email,
    get_alert_log,
    get_student_shortage_data,
    EMAIL_CONFIG,
)

app = Flask(__name__)
app.secret_key = 'smart_academic_tracker_secret_2024'

# ─── DB CONNECTION ────────────────────────────────────────────────────────────
def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='root',
        database='academic_tracker2'
    )

# ─── AUTH DECORATORS ──────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') not in roles:
                flash('Access denied.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator

# ─── LOGIN / LOGOUT ───────────────────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE email=%s AND password=%s", (email, password))
        user = cur.fetchone()
        db.close()
        if user:
            session['user_id'] = user['id']
            session['name']    = user['name']
            session['email']   = user['email']
            session['role']    = user['role']
            # store role-specific id
            db2 = get_db(); c2 = db2.cursor(dictionary=True)
            if user['role'] == 'student':
                c2.execute("SELECT student_id, dept_id, semester, rollno FROM students WHERE user_id=%s", (user['id'],))
                s = c2.fetchone()
                if s:
                    session['student_id'] = s['student_id']
                    session['dept_id']    = s['dept_id']
                    session['semester']   = s['semester']
                    session['rollno']     = s['rollno']
            elif user['role'] == 'faculty':
                c2.execute("SELECT faculty_id, dept_id FROM faculties WHERE user_id=%s", (user['id'],))
                f = c2.fetchone()
                if f:
                    session['faculty_id'] = f['faculty_id']
                    session['dept_id']    = f['dept_id']
            elif user['role'] == 'admin':
                c2.execute("SELECT admin_id FROM admins WHERE user_id=%s", (user['id'],))
                a = c2.fetchone()
                if a:
                    session['admin_id'] = a['admin_id']
            db2.close()
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── DASHBOARD ROUTER ─────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    role = session.get('role')
    if role == 'student':
        return redirect(url_for('student_dashboard'))
    elif role == 'faculty':
        return redirect(url_for('faculty_dashboard'))
    elif role == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('login'))

# ══════════════════════════════════════════════════════════════════════════════
#  STUDENT ROUTES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/student/dashboard')
@login_required
@role_required('student')
def student_dashboard():
    db = get_db(); cur = db.cursor(dictionary=True)
    
    # FIXED: Use user_id instead of student_id to match the DB tables
    uid = session['user_id']
    sem_id = session.get('semester')

    # attendance per subject
    cur.execute("""
        SELECT s.sub_name,
               COUNT(*) AS total,
               SUM(a.status='present') AS present
        FROM attendance a
        JOIN subjects s ON a.subject_id = s.sub_id
        WHERE a.student_id = %s
        GROUP BY s.sub_id, s.sub_name
    """, (uid,))
    att_rows = cur.fetchall()

    # marks per subject
    cur.execute("""
        SELECT s.sub_name,
               SUM(m.marks_obtained) AS obtained,
               SUM(m.total_marks)    AS total
        FROM marks m
        JOIN subjects s ON m.subject_id = s.sub_id
        WHERE m.student_id = %s
        GROUP BY s.sub_id, s.sub_name
    """, (uid,))
    marks_rows = cur.fetchall()

    # notices
    cur.execute("SELECT * FROM notices ORDER BY date DESC LIMIT 5")
    notices = cur.fetchall()

    # upcoming exams 
    # FIXED: Match sem_id directly instead of searching by text label
    cur.execute("""
        SELECT e.exam_name, e.exam_date, e.total_marks, s.sub_name
        FROM exams e JOIN subjects s ON e.sub_id = s.sub_id
        WHERE e.sem_id = %s
          AND e.exam_date >= CURDATE()
        ORDER BY e.exam_date ASC LIMIT 5
    """, (sem_id,))
    exams = cur.fetchall()
    db.close()

    # build chart data
    att_labels   = [r['sub_name'] for r in att_rows]
    att_pcts     = [float(round(r['present']/r['total']*100, 1)) if r['total'] else 0 for r in att_rows]
    marks_labels = [r['sub_name'] for r in marks_rows]
    marks_pcts   = [float(round(r['obtained']/r['total']*100, 1)) if r['total'] else 0 for r in marks_rows]
    overall_att  = float(round(sum(att_pcts)/len(att_pcts), 1)) if att_pcts else 0

    return render_template('student_dashboard.html',
        att_labels=json.dumps(att_labels),
        att_pcts=json.dumps(att_pcts),
        marks_labels=json.dumps(marks_labels),
        marks_pcts=json.dumps(marks_pcts),
        overall_att=overall_att,
        att_rows=att_rows,
        marks_rows=marks_rows,
        notices=notices,
        exams=exams
    )

@app.route('/student/attendance')
@login_required
@role_required('student')
def student_attendance():
    db = get_db(); cur = db.cursor(dictionary=True)
    # FIXED ID
    uid = session['user_id']
    cur.execute("""
        SELECT a.*, s.sub_name
        FROM attendance a
        JOIN subjects s ON a.subject_id = s.sub_id
        WHERE a.student_id = %s
        ORDER BY a.date DESC
    """, (uid,))
    records = cur.fetchall()
    db.close()
    return render_template('student_attendance.html', records=records)

@app.route('/student/marks')
@login_required
@role_required('student')
def student_marks():
    db = get_db(); cur = db.cursor(dictionary=True)
    # FIXED ID
    uid = session['user_id']
    cur.execute("""
        SELECT m.*, s.sub_name, e.exam_name, e.exam_date
        FROM marks m
        JOIN subjects s ON m.subject_id = s.sub_id
        LEFT JOIN exams e ON m.exam_id = e.exam_id
        WHERE m.student_id = %s
        ORDER BY e.exam_date DESC
    """, (uid,))
    marks = cur.fetchall()
    db.close()
    return render_template('student_marks.html', marks=marks)

@app.route('/student/notices')
@login_required
@role_required('student')
def student_notices():
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("SELECT * FROM notices ORDER BY date DESC")
    notices = cur.fetchall()
    db.close()
    return render_template('student_notices.html', notices=notices)

# ══════════════════════════════════════════════════════════════════════════════
#  FACULTY ROUTES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/faculty/dashboard')
@login_required
@role_required('faculty')
def faculty_dashboard():
    db = get_db(); cur = db.cursor(dictionary=True)
    fid = session['faculty_id']

    # Get subjects for this faculty
    cur.execute("SELECT * FROM subjects WHERE faculty_id=%s", (fid,))
    subjects = cur.fetchall()

    # Get attendance marked today
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM attendance a
        JOIN subjects s ON a.subject_id = s.sub_id
        WHERE s.faculty_id = %s AND a.date = CURDATE()
    """, (fid,))
    row_today = cur.fetchone()
    today_count = row_today['cnt'] if row_today else 0

    # FIXED: Accurately count total enrolled students directly from the students table
    cur.execute("""
        SELECT COUNT(DISTINCT st.student_id) AS cnt 
        FROM students st
        JOIN subjects s ON st.dept_id = s.dept_id AND st.semester = s.sem_id
        WHERE s.faculty_id = %s
    """, (fid,))
    row_total = cur.fetchone()
    total_students = row_total['cnt'] if row_total else 0

    # Get recent notices
    cur.execute("SELECT * FROM notices ORDER BY date DESC LIMIT 3")
    notices = cur.fetchall()
    
    db.close()
    
    return render_template('faculty_dashboard.html',
        subjects=subjects,
        today_count=today_count,
        total_students=total_students,
        notices=notices
    )

@app.route('/faculty/attendance', methods=['GET', 'POST'])
@login_required
@role_required('faculty')
def faculty_attendance():
    db = get_db(); cur = db.cursor(dictionary=True)
    fid = session['faculty_id']
    cur.execute("SELECT * FROM subjects WHERE faculty_id=%s", (fid,))
    subjects = cur.fetchall()

    if request.method == 'POST':
        sub_id      = request.form['subject_id']
        att_date    = request.form['att_date']
        att_time    = request.form.get('att_time', datetime.now().strftime('%H:%M'))
        student_ids = request.form.getlist('student_ids')
        statuses    = request.form.getlist('statuses')

        for sid, status in zip(student_ids, statuses):
            try:
                cur.execute("""
                    INSERT INTO attendance (student_id, subject_id, date, status, time)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE status=%s, time=%s
                """, (sid, sub_id, att_date, status, att_time, status, att_time))
            except Exception as e:
                print(f"Error inserting attendance for user {sid}: {e}")
                
        db.commit()
        flash('Attendance saved successfully!', 'success')

        # Auto-send shortage alerts after attendance is saved
        for sid_str in student_ids:
            try:
                check_and_send_alert(int(sid_str))
            except Exception as e:
                app.logger.warning(f"Alert failed for student {sid_str}: {e}")

    # load students for selected subject
    selected_sub = request.args.get('subject_id') or (subjects[0]['sub_id'] if subjects else None)
    students = []
    if selected_sub:
        # FIXED: We select u.id AS student_id so the HTML form submits the correct User ID
        cur.execute("""
            SELECT u.id AS student_id, u.name, st.rollno
            FROM students st
            JOIN users u ON st.user_id = u.id
            WHERE st.dept_id = (SELECT dept_id FROM subjects WHERE sub_id=%s)
              AND st.semester = (SELECT sem_id FROM subjects WHERE sub_id=%s LIMIT 1)
            ORDER BY st.rollno
        """, (selected_sub, selected_sub))
        students = cur.fetchall()
        
    db.close()
    return render_template('faculty_attendance.html',
        subjects=subjects, students=students,
        selected_sub=selected_sub, today=date.today().isoformat()
    )

@app.route('/faculty/marks', methods=['GET', 'POST'])
@login_required
@role_required('faculty')
def faculty_marks():
    db = get_db(); cur = db.cursor(dictionary=True)
    fid = session['faculty_id']
    cur.execute("SELECT * FROM subjects WHERE faculty_id=%s", (fid,))
    subjects = cur.fetchall()
    cur.execute("SELECT * FROM exams ORDER BY exam_date DESC")
    exams = cur.fetchall()

    if request.method == 'POST':
        sub_id      = request.form['subject_id']
        exam_id     = request.form['exam_id']
        student_ids = request.form.getlist('student_ids')
        obtained    = request.form.getlist('marks_obtained')
        total       = request.form.getlist('total_marks')

        for sid, mo, mt in zip(student_ids, obtained, total):
            if mo.strip():
                try:
                    cur.execute("""
                        INSERT INTO marks (student_id, subject_id, marks_obtained, total_marks, subject, exam_id)
                        VALUES (%s, %s, %s, %s,
                            (SELECT sub_name FROM subjects WHERE sub_id=%s), %s)
                        ON DUPLICATE KEY UPDATE marks_obtained=%s, total_marks=%s
                    """, (sid, sub_id, mo, mt, sub_id, exam_id, mo, mt))
                except Exception as e:
                    print(f"Error inserting marks for user {sid}: {e}")
                    
        db.commit()
        flash('Marks saved successfully!', 'success')

    selected_sub = request.args.get('subject_id') or (subjects[0]['sub_id'] if subjects else None)
    students = []
    if selected_sub:
        # FIXED: We select u.id AS student_id so the HTML form submits the correct User ID
        # FIXED: Added the semester filter so only students in this subject's semester appear
        cur.execute("""
            SELECT u.id AS student_id, u.name, st.rollno
            FROM students st 
            JOIN users u ON st.user_id = u.id
            WHERE st.dept_id = (SELECT dept_id FROM subjects WHERE sub_id=%s)
              AND st.semester = (SELECT sem_id FROM subjects WHERE sub_id=%s LIMIT 1)
            ORDER BY st.rollno
        """, (selected_sub, selected_sub))
        students = cur.fetchall()
        
    db.close()
    return render_template('faculty_marks.html',
        subjects=subjects, exams=exams, students=students, selected_sub=selected_sub)

@app.route('/faculty/notices', methods=['GET', 'POST'])
@login_required
@role_required('faculty')
def faculty_notices():
    db = get_db(); cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        msg = request.form['message']
        cur.execute("INSERT INTO notices (sender_role, message, date) VALUES ('faculty', %s, CURDATE())", (msg,))
        db.commit()
        flash('Notice posted!', 'success')
    cur.execute("SELECT * FROM notices ORDER BY date DESC")
    notices = cur.fetchall()
    db.close()
    return render_template('faculty_notices.html', notices=notices)

# Face recognition attendance
@app.route('/faculty/face-attendance')
@login_required
@role_required('faculty')
def face_attendance_page():
    db = get_db(); cur = db.cursor(dictionary=True)
    fid = session['faculty_id']
    cur.execute("SELECT * FROM subjects WHERE faculty_id=%s", (fid,))
    subjects = cur.fetchall()
    db.close()
    return render_template('face_attendance.html', subjects=subjects, today=date.today().isoformat())

@app.route('/api/face-recognize', methods=['POST'])
@login_required
@role_required('faculty')
def face_recognize():
    """Receive base64 frame, run face recognition, return matched students."""
    data       = request.get_json()
    frame_b64  = data.get('frame', '')
    subject_id = data.get('subject_id')

    # decode frame
    img_bytes = base64.b64decode(frame_b64.split(',')[-1])
    np_arr    = np.frombuffer(img_bytes, np.uint8)
    frame     = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    face_locations = face_recognition.face_locations(rgb_frame)
    face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

    FACES_DIR = os.path.join(os.path.dirname(__file__), 'static', 'faces')
    known_encodings = []
    known_names     = []
    known_ids       = []

    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT st.student_id, u.name, st.rollno
        FROM students st JOIN users u ON st.user_id = u.id
    """)
    all_students = cur.fetchall()

    for s in all_students:
        face_path = os.path.join(FACES_DIR, f"student_{s['student_id']}.jpg")
        if os.path.exists(face_path):
            img = face_recognition.load_image_file(face_path)
            encs = face_recognition.face_encodings(img)
            if encs:
                known_encodings.append(encs[0])
                known_names.append(s['name'])
                known_ids.append(s['student_id'])

    recognized = []
    for enc in face_encodings:
        if not known_encodings:
            break
        matches  = face_recognition.compare_faces(known_encodings, enc, tolerance=0.5)
        distances = face_recognition.face_distance(known_encodings, enc)
        best_idx = int(np.argmin(distances)) if len(distances) else -1
        if best_idx >= 0 and matches[best_idx]:
            recognized.append({
                'student_id': known_ids[best_idx],
                'name': known_names[best_idx],
                'confidence': round((1 - distances[best_idx]) * 100, 1)
            })
    db.close()
    return jsonify({'recognized': recognized, 'face_count': len(face_locations)})

@app.route('/api/save-face-attendance', methods=['POST'])
@login_required
@role_required('faculty')
def save_face_attendance():
    data       = request.get_json()
    subject_id = data.get('subject_id')
    att_date   = data.get('date', date.today().isoformat())
    att_time   = data.get('time', datetime.now().strftime('%H:%M'))
    present_ids = data.get('present_ids', [])

    db = get_db(); cur = db.cursor(dictionary=True)
    
    # FIXED: We fetch BOTH the global user_id (for the DB) and the student_id (to match the face data)
    # FIXED: Added the semester filter to only get students in this specific subject
    cur.execute("""
        SELECT u.id AS user_id, st.student_id 
        FROM students st
        JOIN users u ON st.user_id = u.id
        WHERE st.dept_id = (SELECT dept_id FROM subjects WHERE sub_id=%s)
          AND st.semester = (SELECT sem_id FROM subjects WHERE sub_id=%s LIMIT 1)
    """, (subject_id, subject_id))
    
    all_students = cur.fetchall()

    for student in all_students:
        uid = student['user_id']
        sid = student['student_id']
        
        # Check if their specific student_id is in the list of recognized faces
        # If yes, mark present. If no, mark absent.
        status = 'present' if sid in present_ids else 'absent'
        
        try:
            # We insert using 'uid' (User ID) to satisfy the database rule!
            cur.execute("""
                INSERT INTO attendance (student_id, subject_id, date, status, time)
                VALUES (%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE status=%s, time=%s
            """, (uid, subject_id, att_date, status, att_time, status, att_time))
        except Exception as e:
            print(f"Error saving face attendance for uid {uid}: {e}")
            
    db.commit()
    db.close()
    
    # Auto-send shortage alerts after face attendance is saved
    for student in all_students:
        try:
            check_and_send_alert(student['student_id'])
        except Exception as e:
            app.logger.warning(f"Alert failed for student {student['student_id']}: {e}")
            
    return jsonify({'status': 'ok', 'saved': len(all_students)})

# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN ROUTES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/admin/dashboard')
@login_required
@role_required('admin')
def admin_dashboard():
    db = get_db(); cur = db.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) AS cnt FROM students"); total_students = cur.fetchone()['cnt']
    cur.execute("SELECT COUNT(*) AS cnt FROM faculties"); total_faculty  = cur.fetchone()['cnt']
    cur.execute("SELECT COUNT(*) AS cnt FROM subjects");  total_subjects = cur.fetchone()['cnt']
    cur.execute("SELECT COUNT(*) AS cnt FROM departments"); total_depts  = cur.fetchone()['cnt']

    cur.execute("""
        SELECT d.dept_name,
               COUNT(DISTINCT st.student_id) AS students,
               SUM(a.status='present') AS present,
               COUNT(a.att_id) AS total_att
        FROM departments d
        LEFT JOIN students st ON st.dept_id = d.dept_id
        LEFT JOIN attendance a ON a.student_id = st.user_id
        GROUP BY d.dept_id, d.dept_name
    """)
    dept_stats = cur.fetchall()

    cur.execute("SELECT * FROM notices ORDER BY date DESC LIMIT 5")
    notices = cur.fetchall()
    db.close()

    dept_labels = json.dumps([d['dept_name'] for d in dept_stats])
    dept_students = json.dumps([d['students'] for d in dept_stats])

    return render_template('admin_dashboard.html',
        total_students=total_students,
        total_faculty=total_faculty,
        total_subjects=total_subjects,
        total_depts=total_depts,
        dept_labels=dept_labels,
        dept_students=dept_students,
        dept_stats=dept_stats,
        notices=notices
    )

# ── Admin: Students ──────────────────────────────────────────────────────────
@app.route('/admin/students')
@login_required
@role_required('admin')
def admin_students():
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT u.id, u.name, u.email, st.student_id, st.rollno,
               st.semester, d.dept_name
        FROM students st
        JOIN users u ON st.user_id = u.id
        JOIN departments d ON st.dept_id = d.dept_id
        ORDER BY st.rollno
    """)
    students = cur.fetchall()
    cur.execute("SELECT * FROM departments")
    departments = cur.fetchall()
    cur.execute("SELECT * FROM semesters")
    semesters = cur.fetchall()
    db.close()
    return render_template('admin_students.html',
        students=students, departments=departments, semesters=semesters)

@app.route('/admin/students/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_student():
    db = get_db(); cur = db.cursor()
    name     = request.form['name']
    email    = request.form['email']
    password = request.form['password']
    dept_id  = request.form['dept_id']
    semester = request.form['semester']
    rollno   = request.form['rollno']
    cur.execute("INSERT INTO users (name,email,password,role) VALUES (%s,%s,%s,'student')",
                (name, email, password))
    uid = cur.lastrowid
    cur.execute("INSERT INTO students (user_id,dept_id,semester,rollno) VALUES (%s,%s,%s,%s)",
                (uid, dept_id, semester, rollno))
    db.commit(); db.close()
    flash('Student added!', 'success')
    return redirect(url_for('admin_students'))

@app.route('/admin/students/delete/<int:sid>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_student(sid):
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("SELECT user_id FROM students WHERE student_id=%s", (sid,))
    row = cur.fetchone()
    if row:
        cur.execute("DELETE FROM students WHERE student_id=%s", (sid,))
        cur.execute("DELETE FROM users WHERE id=%s", (row['user_id'],))
        db.commit()
        flash('Student deleted.', 'success')
    db.close()
    return redirect(url_for('admin_students'))

# ── Admin: Faculty ───────────────────────────────────────────────────────────
@app.route('/admin/faculty')
@login_required
@role_required('admin')
def admin_faculty():
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT u.id, u.name, u.email, f.faculty_id, d.dept_name
        FROM faculties f
        JOIN users u ON f.user_id = u.id
        JOIN departments d ON f.dept_id = d.dept_id
    """)
    faculty = cur.fetchall()
    cur.execute("SELECT * FROM departments")
    departments = cur.fetchall()
    db.close()
    return render_template('admin_faculty.html', faculty=faculty, departments=departments)

@app.route('/admin/faculty/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_faculty():
    db = get_db(); cur = db.cursor()
    name     = request.form['name']
    email    = request.form['email']
    password = request.form['password']
    dept_id  = request.form['dept_id']
    cur.execute("INSERT INTO users (name,email,password,role) VALUES (%s,%s,%s,'faculty')",
                (name, email, password))
    uid = cur.lastrowid
    cur.execute("INSERT INTO faculties (user_id,dept_id) VALUES (%s,%s)", (uid, dept_id))
    db.commit(); db.close()
    flash('Faculty added!', 'success')
    return redirect(url_for('admin_faculty'))

@app.route('/admin/faculty/delete/<int:fid>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_faculty(fid):
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("SELECT user_id FROM faculties WHERE faculty_id=%s", (fid,))
    row = cur.fetchone()
    if row:
        cur.execute("DELETE FROM faculties WHERE faculty_id=%s", (fid,))
        cur.execute("DELETE FROM users WHERE id=%s", (row['user_id'],))
        db.commit()
        flash('Faculty deleted.', 'success')
    db.close()
    return redirect(url_for('admin_faculty'))

# ── Admin: Departments ───────────────────────────────────────────────────────
@app.route('/admin/departments', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_departments():
    db = get_db(); cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        name = request.form['dept_name']
        cur.execute("INSERT INTO departments (dept_name) VALUES (%s)", (name,))
        db.commit()
        flash('Department added!', 'success')
    cur.execute("SELECT * FROM departments")
    departments = cur.fetchall()
    db.close()
    return render_template('admin_departments.html', departments=departments)

@app.route('/admin/departments/delete/<int:did>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_department(did):
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM departments WHERE dept_id=%s", (did,))
    db.commit(); db.close()
    flash('Department deleted.', 'success')
    return redirect(url_for('admin_departments'))

# ── Admin: Subjects ──────────────────────────────────────────────────────────
@app.route('/admin/subjects', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_subjects():
    db = get_db(); cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        sub_name   = request.form['sub_name']
        faculty_id = request.form['faculty_id']
        dept_id    = request.form['dept_id']
        sem_id     = request.form['sem_id']
        cur.execute("INSERT INTO subjects (sub_name,faculty_id,dept_id,sem_id) VALUES (%s,%s,%s,%s)",
                    (sub_name, faculty_id, dept_id, sem_id))
        db.commit()
        flash('Subject added!', 'success')
    cur.execute("""
        SELECT s.*, d.dept_name, sm.sem_label, u.name AS faculty_name
        FROM subjects s
        JOIN departments d ON s.dept_id = d.dept_id
        JOIN semesters sm ON s.sem_id = sm.sem_id
        LEFT JOIN faculties f ON s.faculty_id = f.faculty_id
        LEFT JOIN users u ON f.user_id = u.id
    """)
    subjects = cur.fetchall()
    cur.execute("SELECT * FROM departments")
    departments = cur.fetchall()
    cur.execute("""
        SELECT f.faculty_id, u.name FROM faculties f JOIN users u ON f.user_id = u.id
    """)
    faculty = cur.fetchall()
    cur.execute("SELECT * FROM semesters")
    semesters = cur.fetchall()
    db.close()
    return render_template('admin_subjects.html',
        subjects=subjects, departments=departments, faculty=faculty, semesters=semesters)

@app.route('/admin/subjects/delete/<int:sid>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_subject(sid):
    db = get_db()
    cur = db.cursor()
    # Because of ON DELETE CASCADE in your database, deleting a subject 
    # will automatically clean up its attendance, marks, and timetable slots!
    cur.execute("DELETE FROM subjects WHERE sub_id=%s", (sid,))
    db.commit()
    db.close()
    flash('Subject deleted successfully.', 'success')
    return redirect(url_for('admin_subjects'))

# ── Admin: Semesters ─────────────────────────────────────────────────────────
@app.route('/admin/semesters', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_semesters():
    db = get_db(); cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        label = request.form['sem_label']
        cur.execute("INSERT INTO semesters (sem_label) VALUES (%s)", (label,))
        db.commit()
        flash('Semester added!', 'success')
    cur.execute("SELECT * FROM semesters ORDER BY sem_id")
    semesters = cur.fetchall()
    db.close()
    return render_template('admin_semesters.html', semesters=semesters)

# ── Admin: Exams ─────────────────────────────────────────────────────────────
@app.route('/admin/exams', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_exams():
    db = get_db(); cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        exam_name   = request.form['exam_name']
        exam_date   = request.form['exam_date']
        total_marks = request.form['total_marks']
        sub_id      = request.form['sub_id']
        sem_id      = request.form['sem_id']
        cur.execute("""
            INSERT INTO exams (exam_name, exam_date, total_marks, sub_id, sem_id)
            VALUES (%s,%s,%s,%s,%s)
        """, (exam_name, exam_date, total_marks, sub_id, sem_id))
        db.commit()
        flash('Exam added!', 'success')
    cur.execute("""
        SELECT e.*, s.sub_name, sm.sem_label
        FROM exams e
        JOIN subjects s ON e.sub_id = s.sub_id
        JOIN semesters sm ON e.sem_id = sm.sem_id
        ORDER BY e.exam_date DESC
    """)
    exams = cur.fetchall()
    cur.execute("SELECT * FROM subjects")
    subjects = cur.fetchall()
    cur.execute("SELECT * FROM semesters")
    semesters = cur.fetchall()
    db.close()
    return render_template('admin_exams.html', exams=exams, subjects=subjects, semesters=semesters)

# ── Admin: Notices ───────────────────────────────────────────────────────────
@app.route('/admin/notices', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_notices():
    db = get_db(); cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        msg = request.form['message']
        cur.execute("INSERT INTO notices (sender_role, message, date) VALUES ('admin',%s,CURDATE())", (msg,))
        db.commit()
        flash('Notice posted!', 'success')
    cur.execute("SELECT * FROM notices ORDER BY date DESC")
    notices = cur.fetchall()
    db.close()
    return render_template('admin_notices.html', notices=notices)

@app.route('/admin/notices/delete/<int:nid>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_notice(nid):
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM notices WHERE id=%s", (nid,))
    db.commit(); db.close()
    flash('Notice deleted.', 'success')
    return redirect(url_for('admin_notices'))

# ── Admin: Upload student face ───────────────────────────────────────────────
@app.route('/admin/upload-face/<int:sid>', methods=['POST'])
@login_required
@role_required('admin')
def admin_upload_face(sid):
    f = request.files.get('face_image')
    if f:
        FACES_DIR = os.path.join(os.path.dirname(__file__), 'static', 'faces')
        os.makedirs(FACES_DIR, exist_ok=True)
        f.save(os.path.join(FACES_DIR, f'student_{sid}.jpg'))
        flash('Face image uploaded!', 'success')
    return redirect(url_for('admin_students'))


# ── Student Insights / ML Prediction ─────────────────────────────────────────
@app.route('/student/insights')
@login_required
@role_required('student')
def student_insights():
    db = get_db(); cur = db.cursor(dictionary=True)
    sid = session['student_id']
    uid = session['user_id']  # Added this!

    cur.execute("""
        SELECT s.sub_name,
               COUNT(*) AS total,
               SUM(a.status='present') AS present
        FROM attendance a
        JOIN subjects s ON a.subject_id = s.sub_id
        WHERE a.student_id = %s
        GROUP BY s.sub_id, s.sub_name
    """, (uid,)) # Changed sid to uid
    att_data = cur.fetchall()

    cur.execute("""
        SELECT s.sub_name,
               SUM(m.marks_obtained) AS obtained,
               SUM(m.total_marks)    AS total
        FROM marks m
        JOIN subjects s ON m.subject_id = s.sub_id
        WHERE m.student_id = %s
        GROUP BY s.sub_id, s.sub_name
    """, (uid,)) # Changed sid to uid
    marks_data = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*) AS cnt FROM exams WHERE exam_date >= CURDATE()
    """)
    row = cur.fetchone()
    upcoming_exams = int(row['cnt']) if row else 0
    db.close()

    report = analyse_student(
        student_id=sid,
        att_data=att_data,
        marks_data=marks_data,
        upcoming_exams=upcoming_exams
    )
    return render_template('student_insights.html', report=report)


@app.route('/admin/students/save_face_cam', methods=['POST'])
@login_required
@role_required('admin')
def save_face_cam():
    data = request.get_json()
    student_id = data.get('student_id')
    image_data = data.get('image')

    if not student_id or not image_data:
        return jsonify({'status': 'error', 'message': 'Missing data'}), 400

    try:
        # Separate the base64 string from the header
        header, encoded = image_data.split(",", 1)
        image_bytes = base64.b64decode(encoded)

        # Create the faces directory if it doesn't exist
        faces_dir = os.path.join(app.root_path, 'static', 'faces')
        os.makedirs(faces_dir, exist_ok=True)

        # Save the image as student_123.jpg
        filepath = os.path.join(faces_dir, f'student_{student_id}.jpg')
        with open(filepath, 'wb') as f:
            f.write(image_bytes)

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Admin: Email Alerts Management Page ──────────────────────────────────
@app.route('/admin/email-alerts', methods=['GET'])
@login_required
@role_required('admin')
def admin_email_alerts():
    db  = get_db(); cur = db.cursor(dictionary=True)

    # students with current shortage
    threshold = EMAIL_CONFIG['threshold']
    cur.execute("""
            SELECT
                st.student_id, u.name, u.email, st.rollno,
                d.dept_name, st.semester,
                COUNT(sub_att.subject_id) AS total_subjects,
                SUM(CASE WHEN sub_att.att_pct < %s THEN 1 ELSE 0 END) AS shortage_count,
                ROUND(AVG(sub_att.att_pct), 1) AS avg_att
            FROM students st
            JOIN users u ON st.user_id = u.id
            JOIN departments d ON st.dept_id = d.dept_id
            JOIN (
                SELECT student_id,
                       subject_id,
                       ROUND(SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS att_pct
                FROM attendance
                GROUP BY student_id, subject_id
            ) sub_att ON sub_att.student_id = st.user_id
            GROUP BY st.student_id, u.name, u.email, st.rollno, d.dept_name, st.semester
            HAVING shortage_count > 0
            ORDER BY shortage_count DESC, avg_att ASC
        """, (threshold,))
    at_risk_students = cur.fetchall()

    # recent alert log
    log = get_alert_log(limit=30)

    # today's alert count
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM alert_log WHERE DATE(sent_at) = CURDATE()
    """)
    today_count = cur.fetchone()['cnt']

    db.close()
    return render_template('admin_email_alerts.html',
        at_risk_students=at_risk_students,
        alert_log=log,
        today_count=today_count,
        threshold=threshold,
        email_config=EMAIL_CONFIG,
    )


# ── Send alert to one student ─────────────────────────────────────────────
@app.route('/admin/email-alerts/send/<int:sid>', methods=['POST'])
@login_required
@role_required('admin')
def admin_send_alert(sid):
    force  = request.form.get('force', 'false') == 'true'
    result = check_and_send_alert(sid, force=force)
    if result['sent']:
        flash(f"✅ Alert sent to {result['student_name']} ({result['email']}) "
              f"— {result['subjects_count']} subject(s) flagged.", 'success')
    elif result['reason'] == 'already_alerted_today':
        flash('ℹ️ This student was already alerted today. '
              'Use Force Send to override.', 'info')
    elif result['reason'] == 'no_shortage':
        flash('ℹ️ No shortage detected for this student.', 'info')
    else:
        flash(f"❌ Email failed: {result.get('error', 'unknown error')}", 'danger')
    return redirect(url_for('admin_email_alerts'))


# ── Bulk send to all at-risk students ────────────────────────────────────
@app.route('/admin/email-alerts/send-all', methods=['POST'])
@login_required
@role_required('admin')
def admin_send_bulk_alerts():
    force   = request.form.get('force', 'false') == 'true'
    results = send_bulk_alerts(force=force)
    flash(
        f"📧 Bulk alert complete — "
        f"Sent: {results['sent']} | "
        f"Skipped: {results['skipped']} | "
        f"Errors: {results['errors']}",
        'success' if results['errors'] == 0 else 'warning'
    )
    return redirect(url_for('admin_email_alerts'))


# ── Send test email ───────────────────────────────────────────────────────
@app.route('/admin/email-alerts/test', methods=['POST'])
@login_required
@role_required('admin')
def admin_test_email():
    to_email = request.form.get('test_email', '').strip()
    if not to_email:
        flash('Please enter a valid email address.', 'danger')
        return redirect(url_for('admin_email_alerts'))
    success, error = send_test_email(to_email)
    if success:
        flash(f'✅ Test email sent to {to_email}. Check your inbox!', 'success')
    else:
        flash(f'❌ Test failed: {error}', 'danger')
    return redirect(url_for('admin_email_alerts'))


# ── Preview shortage data (AJAX — returns JSON) ───────────────────────────
@app.route('/admin/email-alerts/preview/<int:sid>')
@login_required
@role_required('admin')
def admin_preview_alert(sid):
    data = get_student_shortage_data(sid)
    if not data:
        return jsonify({'has_shortage': False})
    return jsonify({
        'has_shortage':    True,
        'student_name':    data['student']['name'],
        'email':           data['student']['email'],
        'threshold':       data['threshold'],
        'subjects':        [
            {
                'sub_name':     s['sub_name'],
                'att_pct':      float(s['att_pct']),
                'attended':     int(s['attended']) if s['attended'] else 0,
                'total_classes': int(s['total_classes']),
            }
            for s in data['shortage_subjects']
        ],
    })

with app.app_context():
    ensure_alert_log_table()

# ════════════════════════════════════════════════════════════════════════════
# TIMETABLE ROUTES 
# ════════════════════════════════════════════════════════════════════════════

DAYS_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

def _build_timetable_grid(slots):
    """
    Converts flat list of timetable rows into a grid dict:
    { day: { 'HH:MM': slot_row } }
    Also returns sorted unique time labels.
    """
    grid = {d: {} for d in DAYS_ORDER}
    times = set()
    for s in slots:
        t = s['start_time']
        # MySQL TIME → timedelta or str — normalise to 'HH:MM'
        if hasattr(t, 'seconds'):          # timedelta
            total = t.seconds
            hh, mm = divmod(total // 60, 60)
            t_key = f"{hh:02d}:{mm:02d}"
        else:
            t_key = str(t)[:5]
        times.add(t_key)
        grid[s['day_of_week']][t_key] = s
    return grid, sorted(times)


# ── Student: view own timetable ───────────────────────────────────────────
@app.route('/student/timetable')
@login_required
@role_required('student')
def student_timetable():
    db = get_db(); cur = db.cursor(dictionary=True)
    sid = session['student_id']

    cur.execute("""
        SELECT st.dept_id, st.semester FROM students st WHERE st.student_id = %s
    """, (sid,))
    info = cur.fetchone()
    if not info:
        db.close()
        flash('Student record not found.', 'danger')
        return redirect(url_for('student_dashboard'))

    cur.execute("""
        SELECT
            t.tt_id, t.day_of_week, t.start_time, t.end_time, t.room,
            s.sub_name, s.sub_id,
            u.name  AS faculty_name,
            d.dept_name,
            sm.sem_label
        FROM timetable t
        JOIN subjects    s  ON t.sub_id  = s.sub_id
        JOIN faculties   f  ON s.faculty_id = f.faculty_id
        JOIN users       u  ON f.user_id = u.id
        JOIN departments d  ON t.dept_id = d.dept_id
        JOIN semesters   sm ON t.sem_id  = sm.sem_id
        WHERE t.dept_id = %s
          AND t.sem_id  = (
              SELECT sem_id FROM semesters
              WHERE sem_label = %s OR sem_id = %s
              LIMIT 1
          )
        ORDER BY FIELD(t.day_of_week,
            'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'),
            t.start_time
    """, (info['dept_id'], str(info['semester']), info['semester']))
    slots = cur.fetchall()
    db.close()

    grid, times = _build_timetable_grid(slots)
    today = datetime.now().strftime('%A')

    return render_template('timetable.html',
        grid=grid, times=times, days=DAYS_ORDER,
        today=today, role='student', slots=slots)


# ── Faculty: view own classes timetable ──────────────────────────────────
@app.route('/faculty/timetable')
@login_required
@role_required('faculty')
def faculty_timetable():
    db = get_db(); cur = db.cursor(dictionary=True)
    fid = session['faculty_id']

    cur.execute("""
        SELECT
            t.tt_id, t.day_of_week, t.start_time, t.end_time, t.room,
            s.sub_name, s.sub_id,
            u.name  AS faculty_name,
            d.dept_name,
            sm.sem_label
        FROM timetable t
        JOIN subjects    s  ON t.sub_id  = s.sub_id
        JOIN faculties   f  ON s.faculty_id = f.faculty_id
        JOIN users       u  ON f.user_id = u.id
        JOIN departments d  ON t.dept_id = d.dept_id
        JOIN semesters   sm ON t.sem_id  = sm.sem_id
        WHERE s.faculty_id = %s
        ORDER BY FIELD(t.day_of_week,
            'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'),
            t.start_time
    """, (fid,))
    slots = cur.fetchall()
    db.close()

    grid, times = _build_timetable_grid(slots)
    today = datetime.now().strftime('%A')

    return render_template('timetable.html',
        grid=grid, times=times, days=DAYS_ORDER,
        today=today, role='faculty', slots=slots)


# ── Admin: manage full timetable ─────────────────────────────────────────
@app.route('/admin/timetable', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_timetable():
    db = get_db(); cur = db.cursor(dictionary=True)

    if request.method == 'POST':
        action = request.form.get('action', 'add')

        if action == 'add':
            sub_id     = request.form['sub_id']
            dept_id    = request.form['dept_id']
            sem_id     = request.form['sem_id']
            day        = request.form['day_of_week']
            start_time = request.form['start_time']
            end_time   = request.form['end_time']
            room       = request.form.get('room', '').strip() or None

            # validate end > start
            if start_time >= end_time:
                flash('End time must be after start time.', 'danger')
            else:
                try:
                    cur.execute("""
                        INSERT INTO timetable
                            (sub_id, dept_id, sem_id, day_of_week, start_time, end_time, room)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (sub_id, dept_id, sem_id, day, start_time, end_time, room))
                    db.commit()
                    flash('Timetable slot added!', 'success')
                except Exception as e:
                    db.rollback()
                    flash(f'Error: {str(e)} — slot may conflict with an existing one.', 'danger')

        elif action == 'delete':
            tt_id = request.form['tt_id']
            cur.execute("DELETE FROM timetable WHERE tt_id = %s", (tt_id,))
            db.commit()
            flash('Slot deleted.', 'success')

    # filter params
    f_dept = request.args.get('dept_id', '')
    f_sem  = request.args.get('sem_id', '')

    query = """
        SELECT
            t.tt_id, t.day_of_week, t.start_time, t.end_time, t.room,
            s.sub_name, s.sub_id,
            u.name  AS faculty_name,
            d.dept_name, d.dept_id,
            sm.sem_label, sm.sem_id
        FROM timetable t
        JOIN subjects    s  ON t.sub_id  = s.sub_id
        JOIN faculties   f  ON s.faculty_id = f.faculty_id
        JOIN users       u  ON f.user_id = u.id
        JOIN departments d  ON t.dept_id = d.dept_id
        JOIN semesters   sm ON t.sem_id  = sm.sem_id
        WHERE 1=1
    """
    params = []
    if f_dept:
        query += " AND t.dept_id = %s"; params.append(f_dept)
    if f_sem:
        query += " AND t.sem_id = %s";  params.append(f_sem)
    query += """ ORDER BY FIELD(t.day_of_week,
        'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'),
        t.start_time"""

    cur.execute(query, params)
    slots = cur.fetchall()

    cur.execute("SELECT * FROM subjects ORDER BY sub_name")
    subjects = cur.fetchall()
    cur.execute("SELECT * FROM departments")
    departments = cur.fetchall()
    cur.execute("SELECT * FROM semesters ORDER BY sem_id")
    semesters = cur.fetchall()
    db.close()

    grid, times = _build_timetable_grid(slots)
    today = datetime.now().strftime('%A')

    return render_template('timetable.html',
        grid=grid, times=times, days=DAYS_ORDER,
        today=today, role='admin', slots=slots,
        subjects=subjects, departments=departments, semesters=semesters,
        f_dept=f_dept, f_sem=f_sem)


# ── Admin: Reports & Analytics ───────────────────────────────────────────────
@app.route('/admin/reports')
@login_required
@role_required('admin')
def admin_reports():
    db = get_db()
    cur = db.cursor(dictionary=True)
    
    # Check which report the admin selected in the dropdown (defaults to attendance)
    report_type = request.args.get('type', 'attendance')
    
    # 1. Get Top Card Metrics (Universal)
    cur.execute("SELECT COUNT(*) as count FROM students")
    total_students = cur.fetchone()['count']
    
    cur.execute("SELECT COUNT(*) as count FROM faculties")
    total_faculty = cur.fetchone()['count']
    
    cur.execute("SELECT COUNT(*) as count FROM alert_log")
    active_alerts = cur.fetchone()['count']

    # 2. Fetch the specific report data
    defaulters = []
    marks_data = []
    dynamic_red_card = 0
    
    if report_type == 'attendance':
        defaulters_query = """
            SELECT s.rollno, u.name AS student_name, sub.sub_name,
            ROUND((SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) / COUNT(a.att_id)) * 100, 2) AS att_percent
            FROM students s
            JOIN users u ON s.user_id = u.id
            JOIN attendance a ON a.student_id = u.id
            JOIN subjects sub ON a.subject_id = sub.sub_id
            GROUP BY s.student_id, sub.sub_id
            HAVING att_percent < 75.0
            ORDER BY att_percent ASC
        """
        cur.execute(defaulters_query)
        defaulters = cur.fetchall()
        dynamic_red_card = len(defaulters)  # Count of Attendance Defaulters
        
    elif report_type == 'marks':
        marks_query = """
            SELECT s.rollno, u.name AS student_name, sub.sub_name, e.exam_name,
            m.marks_obtained, m.total_marks,
            ROUND((m.marks_obtained / m.total_marks) * 100, 2) AS percentage
            FROM marks m
            JOIN students s ON m.student_id = s.user_id
            JOIN users u ON s.user_id = u.id
            JOIN subjects sub ON m.subject_id = sub.sub_id
            JOIN exams e ON m.exam_id = e.exam_id
            ORDER BY e.exam_date DESC, s.rollno ASC
            LIMIT 100
        """
        cur.execute(marks_query)
        marks_data = cur.fetchall()
        # Count failed students (below 35%) for the red card
        dynamic_red_card = sum(1 for row in marks_data if row['percentage'] < 35.0)

    # 3. Get Subject Performance Data for the Chart (Remains Universal)
    chart_query = """
        SELECT sub.sub_name, 
        ROUND(AVG((m.marks_obtained / m.total_marks) * 100), 1) as avg_score 
        FROM marks m 
        JOIN subjects sub ON m.subject_id = sub.sub_id 
        GROUP BY sub.sub_id
    """
    cur.execute(chart_query)
    chart_data = cur.fetchall()
    
    chart_labels = json.dumps([row['sub_name'] for row in chart_data])
    chart_scores = json.dumps([float(row['avg_score']) if row['avg_score'] else 0 for row in chart_data])

    db.close()

    return render_template('admin_reports.html', 
                           report_type=report_type,
                           total_students=total_students, 
                           total_faculty=total_faculty,
                           active_alerts=active_alerts,
                           dynamic_red_card=dynamic_red_card,
                           defaulters=defaulters,
                           marks_data=marks_data,
                           chart_labels=chart_labels,
                           chart_scores=chart_scores)



if __name__ == '__main__':
    os.makedirs('static/faces', exist_ok=True)
    app.run(debug=True, port=5000)