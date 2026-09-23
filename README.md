# Smart Academic Tracker

Flask + MySQL web app with face recognition attendance, analytics dashboard, and role-based access.

## Tables (academic_tracker2)
| Table       | Key Columns                                          |
|-------------|------------------------------------------------------|
| users       | id, name, email, password, role                      |
| students    | student_id, user_id, dept_id, semester, rollno       |
| admins      | admin_id, user_id                                    |
| faculties   | faculty_id, user_id, dept_id                         |
| departments | dept_id, dept_name                                   |
| semesters   | sem_id, sem_label                                    |
| subjects    | sub_id, sub_name, faculty_id, dept_id, sem_id        |
| attendance  | att_id, student_id, subject_id, date, status, time   |
| exams       | exam_id, exam_name, exam_date, total_marks, sub_id, sem_id |
| marks       | mark_id, student_id, subject_id, marks_obtained, total_marks, subject, exam_id |
| notices     | id, sender_role, message, date                       |

## Setup

### 1. Run the SQL script
Open MySQL Workbench → run `setup_db.sql` on your `academic_tracker2` database.

### 2. Install Python packages
```
pip install -r requirements.txt
```
> Note: `face_recognition` requires cmake + dlib. On Windows install cmake first.

### 3. Run the app
```
python app.py
```

## Demo Login Accounts
| Role    | Email                  | Password    |
|---------|------------------------|-------------|
| Admin   | admin@college.edu      | admin123    |
| Faculty | faculty@college.edu    | faculty123  |
| Student | student@college.edu    | student123  |

## Project Structure
```
smart_academic_tracker/
├── app.py                  ← Main Flask app (all routes)
├── requirements.txt
├── setup_db.sql            ← Run this first in MySQL Workbench
├── static/
│   └── faces/              ← Student face images (student_<id>.jpg)
└── templates/
    ├── base.html
    ├── login.html
    ├── student_dashboard.html
    ├── student_attendance.html
    ├── student_marks.html
    ├── student_notices.html
    ├── faculty_dashboard.html
    ├── faculty_attendance.html
    ├── faculty_marks.html
    ├── faculty_notices.html
    ├── face_attendance.html
    ├── admin_dashboard.html
    ├── admin_students.html
    ├── admin_faculty.html
    ├── admin_departments.html
    ├── admin_subjects.html
    ├── admin_semesters.html
    ├── admin_exams.html
    └── admin_notices.html
```

## Face Recognition Setup
1. Admin logs in → Students → camera icon next to a student
2. Upload a clear front-face JPG photo
3. Faculty uses Face Attendance page to mark attendance via webcam
