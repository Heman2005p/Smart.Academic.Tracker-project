"""
email_alerts.py  –  Smart Academic Tracker
==========================================
Attendance shortage email alert system.
Uses only Python stdlib (smtplib + email.mime) — no extra packages needed.

HOW IT WORKS
────────────
1. check_and_send_alerts(student_id)
   Call this from any route after attendance is saved.
   Fetches per-subject attendance for that student, finds subjects below
   the threshold, and sends ONE consolidated email if any shortage exists.

2. send_bulk_alerts()
   Admin-triggered: scans ALL students, sends alerts to every student
   who has at least one subject below threshold and hasn't been alerted
   today already (tracked via the alert_log table).

3. send_test_email(to_address)
   Admin can verify SMTP config is working before enabling alerts.

CONFIGURATION
─────────────
Set these in email_config dict below, OR load from environment variables.
Supports Gmail, Outlook, any generic SMTP relay.
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime
import mysql.connector

logger = logging.getLogger(__name__)

# ─── SMTP Configuration ──────────────────────────────────────────────────────
# Edit these values OR set as environment variables
import os

EMAIL_CONFIG = {
    'smtp_host':     os.environ.get('SMTP_HOST',     'smtp.gmail.com'),
    'smtp_port':     int(os.environ.get('SMTP_PORT', 587)),
    'use_tls':       os.environ.get('SMTP_TLS',      'true').lower() == 'true',
    'sender_email':  os.environ.get('SENDER_EMAIL',  'jgadmin0@gmail.com'),
    'sender_password': os.environ.get('SENDER_PASSWORD', 'obshxtgmodagvtvn'),
    'sender_name':   os.environ.get('SENDER_NAME',   'Smart Academic Tracker'),
    'threshold':     int(os.environ.get('ATT_THRESHOLD', 75)),   # alert below this %
}

# ─── DB helper (mirrors app.py) ──────────────────────────────────────────────
def _get_db():
    return mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ.get('DB_PASS', 'root'),
        database='academic_tracker2'
    )


# =============================================================================
#  ENSURE alert_log TABLE EXISTS
# =============================================================================

def ensure_alert_log_table():
    """
    Creates alert_log if it doesn't exist.
    Call once at app startup (in app.py before app.run).
    """
    db = _get_db()
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_log (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            student_id  INT  NOT NULL,
            sent_at     DATETIME NOT NULL,
            subject_count INT NOT NULL DEFAULT 1,
            alert_type  VARCHAR(30) DEFAULT 'shortage',
            INDEX idx_student_date (student_id, sent_at)
        )
    """)
    db.commit()
    db.close()


# =============================================================================
#  CORE: FETCH SHORTAGE DATA FOR ONE STUDENT
# =============================================================================

def get_student_shortage_data(student_id):
    """
    Returns dict with student info + list of subjects below threshold.
    Returns None if student has no attendance data yet.
    """
    db = _get_db()
    cur = db.cursor(dictionary=True)
    threshold = EMAIL_CONFIG['threshold']

    # student + user info
    cur.execute("""
        SELECT u.name, u.email, st.rollno, d.dept_name, st.semester
        FROM students st
        JOIN users u ON st.user_id = u.id
        JOIN departments d ON st.dept_id = d.dept_id
        WHERE st.student_id = %s
    """, (student_id,))
    student = cur.fetchone()

    if not student or not student.get('email'):
        db.close()
        return None

    # per-subject attendance
    cur.execute("""
        SELECT
            s.sub_name,
            COUNT(*)                   AS total_classes,
            SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END)  AS attended,
            ROUND(SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS att_pct
        FROM attendance a
        JOIN subjects s ON a.subject_id = s.sub_id
        WHERE a.student_id = (SELECT user_id FROM students WHERE student_id = %s)
        GROUP BY s.sub_id, s.sub_name
        HAVING att_pct < %s
        ORDER BY att_pct ASC
    """, (student_id, threshold))
    shortage_subjects = cur.fetchall()

    # also fetch full subject list (for context in email)
    cur.execute("""
        SELECT
            s.sub_name,
            COUNT(*)                   AS total_classes,
            SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END)  AS attended,
            ROUND(SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS att_pct
        FROM attendance a
        JOIN subjects s ON a.subject_id = s.sub_id
        WHERE a.student_id = (SELECT user_id FROM students WHERE student_id = %s)
        GROUP BY s.sub_id, s.sub_name
        ORDER BY att_pct ASC
    """, (student_id,))
    all_subjects = cur.fetchall()

    db.close()

    if not shortage_subjects:
        return None

    return {
        'student':          student,
        'shortage_subjects': shortage_subjects,
        'all_subjects':     all_subjects,
        'threshold':        threshold,
    }


# =============================================================================
#  CHECK IF ALREADY ALERTED TODAY
# =============================================================================

def _already_alerted_today(student_id):
    db = _get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM alert_log
        WHERE student_id = %s AND DATE(sent_at) = %s
    """, (student_id, date.today()))
    count = cur.fetchone()[0]
    db.close()
    return count > 0


def _log_alert(student_id, subject_count):
    db = _get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO alert_log (student_id, sent_at, subject_count)
        VALUES (%s, %s, %s)
    """, (student_id, datetime.now(), subject_count))
    db.commit()
    db.close()


# =============================================================================
#  EMAIL BUILDER
# =============================================================================

def _build_email(data):
    """Builds a clean HTML + plain-text MIMEMultipart email."""
    student   = data['student']
    subjects  = data['shortage_subjects']
    threshold = data['threshold']
    name      = student['name']
    rollno    = student.get('rollno', 'N/A')

    # ── Plain text version ────────────────────────────────────────────────────
    text_lines = [
        f"Dear {name},",
        "",
        f"This is an automated attendance alert from Smart Academic Tracker.",
        f"Your attendance has fallen below the required {threshold}% threshold",
        f"in {len(subjects)} subject(s).",
        "",
        "SUBJECTS BELOW THRESHOLD:",
    ]
    for s in subjects:
        attended = int(s['attended']) if s['attended'] else 0
        total    = int(s['total_classes'])
        pct      = float(s['att_pct'])
        classes_needed = max(0, int((threshold / 100 * total - attended) / (1 - threshold / 100)) + 1)
        text_lines.append(
            f"  • {s['sub_name']}: {pct}% ({attended}/{total} classes) "
            f"— need {classes_needed} more classes to reach {threshold}%"
        )
    text_lines += [
        "",
        "Please attend all upcoming classes to avoid exam debarment.",
        "",
        "— Smart Academic Tracker",
    ]
    plain_text = "\n".join(text_lines)

    # ── HTML version ──────────────────────────────────────────────────────────
    subject_rows = ""
    for s in subjects:
        attended = int(s['attended']) if s['attended'] else 0
        total    = int(s['total_classes'])
        pct      = float(s['att_pct'])
        if total > 0 and threshold / 100 < 1:
            classes_needed = max(0, int((threshold / 100 * total - attended) / (1 - threshold / 100)) + 1)
        else:
            classes_needed = 0

        if pct < 60:
            bar_color  = '#ef4444'
            risk_label = 'CRITICAL'
            risk_bg    = '#fee2e2'
            risk_color = '#991b1b'
        elif pct < 70:
            bar_color  = '#f59e0b'
            risk_label = 'HIGH RISK'
            risk_bg    = '#fef3c7'
            risk_color = '#92400e'
        else:
            bar_color  = '#f97316'
            risk_label = 'AT RISK'
            risk_bg    = '#ffedd5'
            risk_color = '#9a3412'

        bar_width = max(2, int(pct))

        subject_rows += f"""
        <tr>
          <td style="padding:14px 16px;border-bottom:1px solid #f1f5f9">
            <div style="font-weight:600;color:#1e293b;margin-bottom:4px">{s['sub_name']}</div>
            <div style="background:#e2e8f0;border-radius:99px;height:6px;overflow:hidden;margin-bottom:6px">
              <div style="width:{bar_width}%;background:{bar_color};height:100%;border-radius:99px"></div>
            </div>
            <div style="font-size:12px;color:#64748b">
              {attended} / {total} classes attended
              &nbsp;·&nbsp;
              Need <strong>{classes_needed} more</strong> to reach {threshold}%
            </div>
          </td>
          <td style="padding:14px 16px;border-bottom:1px solid #f1f5f9;text-align:center;white-space:nowrap">
            <div style="font-size:22px;font-weight:800;color:{bar_color}">{pct}%</div>
            <span style="display:inline-block;background:{risk_bg};color:{risk_color};
                         font-size:10px;font-weight:700;padding:2px 8px;border-radius:99px;
                         letter-spacing:0.5px;margin-top:4px">{risk_label}</span>
          </td>
        </tr>"""

    # overall attendance stats line
    all_subj = data.get('all_subjects', [])
    if all_subj:
        avg_att = round(sum(float(s['att_pct']) for s in all_subj) / len(all_subj), 1)
        stats_line = f"Overall average attendance: <strong>{avg_att}%</strong> across {len(all_subj)} subjects"
    else:
        stats_line = ""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">

<div style="max-width:580px;margin:32px auto;background:#fff;border-radius:16px;
            overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08)">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 100%);padding:28px 32px">
    <div style="display:flex;align-items:center;gap:12px">
      <div style="width:42px;height:42px;background:rgba(255,255,255,0.15);border-radius:12px;
                  display:flex;align-items:center;justify-content:center;font-size:22px">🎓</div>
      <div>
        <div style="color:#fff;font-size:16px;font-weight:700">Smart Academic Tracker</div>
        <div style="color:rgba(255,255,255,0.6);font-size:12px">Attendance Alert System</div>
      </div>
    </div>
  </div>

  <!-- Alert Banner -->
  <div style="background:#fef2f2;border-left:4px solid #ef4444;padding:16px 32px;
              display:flex;align-items:center;gap:12px">
    <span style="font-size:20px">⚠️</span>
    <div>
      <div style="font-weight:700;color:#991b1b;font-size:14px">ATTENDANCE SHORTAGE ALERT</div>
      <div style="color:#b91c1c;font-size:12px;margin-top:2px">
        {len(subjects)} subject(s) below {threshold}% minimum requirement
      </div>
    </div>
  </div>

  <!-- Body -->
  <div style="padding:24px 32px">
    <p style="color:#1e293b;font-size:15px;margin:0 0 6px">Dear <strong>{name}</strong>,</p>
    <p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 20px">
      This is an automated alert from your institution's academic tracking system.
      Your attendance has dropped below the required <strong>{threshold}%</strong> threshold
      in the following subject(s). Please take immediate action to avoid exam debarment.
    </p>

    <!-- Student info pill -->
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                padding:12px 16px;margin-bottom:20px;display:flex;gap:24px;flex-wrap:wrap">
      <div><span style="font-size:11px;color:#94a3b8;display:block">ROLL NO</span>
           <span style="font-size:13px;font-weight:600;color:#1e293b">{rollno}</span></div>
      <div><span style="font-size:11px;color:#94a3b8;display:block">DEPARTMENT</span>
           <span style="font-size:13px;font-weight:600;color:#1e293b">{student.get('dept_name','N/A')}</span></div>
      <div><span style="font-size:11px;color:#94a3b8;display:block">SEMESTER</span>
           <span style="font-size:13px;font-weight:600;color:#1e293b">{student.get('semester','N/A')}</span></div>
      <div><span style="font-size:11px;color:#94a3b8;display:block">ALERT DATE</span>
           <span style="font-size:13px;font-weight:600;color:#1e293b">{date.today().strftime('%d %b %Y')}</span></div>
    </div>

    <!-- Subject table -->
    <table style="width:100%;border-collapse:collapse;border:1px solid #f1f5f9;border-radius:12px;overflow:hidden;margin-bottom:20px">
      <thead>
        <tr style="background:#f8fafc">
          <th style="padding:12px 16px;text-align:left;font-size:11px;font-weight:700;
                     color:#64748b;letter-spacing:0.5px;text-transform:uppercase">Subject</th>
          <th style="padding:12px 16px;text-align:center;font-size:11px;font-weight:700;
                     color:#64748b;letter-spacing:0.5px;text-transform:uppercase">Attendance</th>
        </tr>
      </thead>
      <tbody>{subject_rows}</tbody>
    </table>

    {f'<p style="color:#64748b;font-size:13px;margin:0 0 20px">{stats_line}</p>' if stats_line else ''}

    <!-- Action box -->
    <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:12px;padding:16px 20px;margin-bottom:20px">
      <div style="font-weight:700;color:#92400e;font-size:13px;margin-bottom:8px">
        📋 Immediate Action Required
      </div>
      <ul style="margin:0;padding-left:18px;color:#78350f;font-size:13px;line-height:1.8">
        <li>Attend <strong>all remaining classes</strong> in subjects listed above</li>
        <li>Contact your subject faculty to discuss your attendance status</li>
        <li>Check your Smart Academic Tracker dashboard for detailed forecasts</li>
        <li>Missing even one more class in critical subjects may trigger exam debarment</li>
      </ul>
    </div>

    <p style="color:#94a3b8;font-size:12px;line-height:1.6;margin:0">
      This is an automated message from Smart Academic Tracker. Do not reply to this email.
      Log in to your student account to view detailed attendance records and AI-powered insights.
    </p>
  </div>

  <!-- Footer -->
  <div style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:16px 32px;
              text-align:center">
    <p style="color:#94a3b8;font-size:11px;margin:0">
      Smart Academic Tracker &nbsp;·&nbsp; Automated Attendance Monitoring System
      &nbsp;·&nbsp; {date.today().year}
    </p>
  </div>
</div>

</body>
</html>"""

    return plain_text, html


# =============================================================================
#  SEND EMAIL (core SMTP function)
# =============================================================================

def _send_smtp(to_email, to_name, subject, plain_text, html_body):
    """
    Sends email via SMTP. Returns (success: bool, error_msg: str).
    """
    cfg = EMAIL_CONFIG
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f"{cfg['sender_name']} <{cfg['sender_email']}>"
    msg['To']      = f"{to_name} <{to_email}>"

    msg.attach(MIMEText(plain_text, 'plain'))
    msg.attach(MIMEText(html_body,  'html'))

    try:
        if cfg['use_tls']:
            server = smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port'], timeout=10)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(cfg['smtp_host'], cfg['smtp_port'], timeout=10)
            server.ehlo()

        server.login(cfg['sender_email'], cfg['sender_password'])
        server.sendmail(cfg['sender_email'], to_email, msg.as_string())
        server.quit()
        logger.info(f"Alert sent to {to_email}")
        return True, ""

    except smtplib.SMTPAuthenticationError:
        err = "SMTP authentication failed. Check sender_email and sender_password in EMAIL_CONFIG."
        logger.error(err)
        return False, err
    except smtplib.SMTPRecipientsRefused:
        err = f"Recipient refused: {to_email}"
        logger.error(err)
        return False, err
    except smtplib.SMTPException as e:
        err = f"SMTP error: {str(e)}"
        logger.error(err)
        return False, err
    except Exception as e:
        err = f"Unexpected error: {str(e)}"
        logger.error(err)
        return False, err


# =============================================================================
#  PUBLIC API
# =============================================================================

def check_and_send_alert(student_id, force=False):
    """
    Check one student and send alert if shortage exists and not already
    alerted today (unless force=True).

    Returns: dict with status info
    """
    if not force and _already_alerted_today(student_id):
        return {'sent': False, 'reason': 'already_alerted_today'}

    data = get_student_shortage_data(student_id)
    if data is None:
        return {'sent': False, 'reason': 'no_shortage'}

    student    = data['student']
    to_email   = student['email']
    to_name    = student['name']
    n_subjects = len(data['shortage_subjects'])

    subject_line = (
        f"⚠️ Attendance Alert: {n_subjects} subject(s) below "
        f"{data['threshold']}% — Action Required"
    )

    plain_text, html_body = _build_email(data)
    success, error = _send_smtp(to_email, to_name, subject_line, plain_text, html_body)

    if success:
        _log_alert(student_id, n_subjects)

    return {
        'sent':          success,
        'email':         to_email,
        'student_name':  to_name,
        'subjects_count': n_subjects,
        'error':         error if not success else None,
        'reason':        'sent' if success else 'smtp_error',
    }


def send_bulk_alerts(force=False):
    """
    Send alerts to ALL students who have shortage. Admin-triggered.
    Returns summary dict.
    """
    db  = _get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT student_id FROM students")
    all_students = cur.fetchall()
    db.close()

    results = {'sent': 0, 'skipped': 0, 'errors': 0, 'details': []}

    for row in all_students:
        sid    = row['student_id']
        result = check_and_send_alert(sid, force=force)
        results['details'].append({'student_id': sid, **result})

        if result['sent']:
            results['sent'] += 1
        elif result['reason'] in ('no_shortage', 'already_alerted_today'):
            results['skipped'] += 1
        else:
            results['errors'] += 1

    return results


def send_test_email(to_email):
    """
    Send a test email to verify SMTP config. Used by admin.
    Returns (success, error_msg).
    """
    cfg     = EMAIL_CONFIG
    subject = "✅ Smart Academic Tracker — SMTP Test"
    plain   = (
        "This is a test email from Smart Academic Tracker.\n"
        "If you received this, your SMTP configuration is working correctly.\n\n"
        f"SMTP Host: {cfg['smtp_host']}:{cfg['smtp_port']}\n"
        f"Sender: {cfg['sender_email']}\n"
        f"TLS: {cfg['use_tls']}\n"
    )
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:32px auto;padding:24px;
                border:1px solid #e2e8f0;border-radius:12px">
      <div style="font-size:32px;text-align:center;margin-bottom:16px">✅</div>
      <h2 style="text-align:center;color:#1e293b;margin:0 0 8px">SMTP Test Successful</h2>
      <p style="text-align:center;color:#64748b;font-size:14px">
        Smart Academic Tracker email alerts are configured correctly.
      </p>
      <div style="background:#f8fafc;border-radius:8px;padding:12px 16px;margin-top:20px;font-size:13px;color:#64748b">
        <div>Host: <strong style="color:#1e293b">{cfg['smtp_host']}:{cfg['smtp_port']}</strong></div>
        <div>Sender: <strong style="color:#1e293b">{cfg['sender_email']}</strong></div>
        <div>TLS: <strong style="color:#1e293b">{cfg['use_tls']}</strong></div>
      </div>
    </div>"""

    return _send_smtp(to_email, "Admin", subject, plain, html)


def get_alert_log(limit=50):
    """Fetch recent alert log entries for the admin panel."""
    db  = _get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT al.id, al.sent_at, al.subject_count, al.alert_type,
               u.name AS student_name, u.email,
               st.rollno, d.dept_name
        FROM alert_log al
        JOIN students st ON al.student_id = st.student_id
        JOIN users u     ON st.user_id = u.id
        JOIN departments d ON st.dept_id = d.dept_id
        ORDER BY al.sent_at DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    db.close()
    return rows
