-- ============================================================
-- Smart Academic Tracker - Database Setup Script
-- Database: academic_tracker2
-- Run this in MySQL Workbench to set up tables + seed data
-- ============================================================

USE academic_tracker2;

-- ── 1. users ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    name     VARCHAR(100),
    email    VARCHAR(100) UNIQUE,
    password VARCHAR(100),
    role     ENUM('admin','faculty','student')
);

-- ── 2. departments ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS departments (
    dept_id   INT AUTO_INCREMENT PRIMARY KEY,
    dept_name VARCHAR(100)
);

-- ── 3. semesters ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS semesters (
    sem_id    INT AUTO_INCREMENT PRIMARY KEY,
    sem_label VARCHAR(50)
);

-- ── 4. students ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS students (
    student_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT,
    dept_id    INT,
    semester   INT,
    rollno     VARCHAR(20),
    FOREIGN KEY (user_id)  REFERENCES users(id)        ON DELETE CASCADE,
    FOREIGN KEY (dept_id)  REFERENCES departments(dept_id)
);

-- ── 5. admins ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS admins (
    admin_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id  INT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ── 6. faculties ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS faculties (
    faculty_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT,
    dept_id    INT,
    FOREIGN KEY (user_id)  REFERENCES users(id)           ON DELETE CASCADE,
    FOREIGN KEY (dept_id)  REFERENCES departments(dept_id)
);

-- ── 7. subjects ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subjects (
    sub_id     INT AUTO_INCREMENT PRIMARY KEY,
    sub_name   VARCHAR(100),
    faculty_id INT,
    dept_id    INT,
    sem_id     INT,
    FOREIGN KEY (faculty_id) REFERENCES faculties(faculty_id),
    FOREIGN KEY (dept_id)    REFERENCES departments(dept_id),
    FOREIGN KEY (sem_id)     REFERENCES semesters(sem_id)
);

-- ── 8. attendance ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attendance (
    att_id     INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT,
    subject_id INT,
    date       DATE,
    status     ENUM('present','absent','late','excused') DEFAULT 'absent',
    time       TIME,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
    FOREIGN KEY (subject_id) REFERENCES subjects(sub_id),
    UNIQUE KEY uq_att (student_id, subject_id, date)
);

-- ── 9. exams ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exams (
    exam_id     INT AUTO_INCREMENT PRIMARY KEY,
    exam_name   VARCHAR(100),
    exam_date   DATE,
    total_marks INT,
    sub_id      INT,
    sem_id      INT,
    FOREIGN KEY (sub_id) REFERENCES subjects(sub_id),
    FOREIGN KEY (sem_id) REFERENCES semesters(sem_id)
);

-- ── 10. marks ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marks (
    mark_id        INT AUTO_INCREMENT PRIMARY KEY,
    student_id     INT,
    subject_id     INT,
    marks_obtained DECIMAL(5,2),
    total_marks    INT,
    subject        VARCHAR(100),
    exam_id        INT,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
    FOREIGN KEY (subject_id) REFERENCES subjects(sub_id),
    FOREIGN KEY (exam_id)    REFERENCES exams(exam_id)
);

-- ── 11. notices ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notices (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    sender_role ENUM('admin','faculty'),
    message     TEXT,
    date        DATE
);


-- ============================================================
-- SEED DATA
-- ============================================================

-- Departments
INSERT IGNORE INTO departments (dept_id, dept_name) VALUES
(1, 'Computer Science'),
(2, 'Information Technology'),
(3, 'Electronics');

-- Semesters
INSERT IGNORE INTO semesters (sem_id, sem_label) VALUES
(1, 'Semester 1'),
(2, 'Semester 2'),
(3, 'Semester 3'),
(4, 'Semester 4'),
(5, 'Semester 5'),
(6, 'Semester 6');

-- Users
INSERT IGNORE INTO users (id, name, email, password, role) VALUES
(1, 'Admin User',      'admin@college.edu',   'admin123',   'admin'),
(2, 'Dr. Priya Sharma','faculty@college.edu',  'faculty123', 'faculty'),
(3, 'Rahul Verma',     'student@college.edu',  'student123', 'student'),
(4, 'Dr. Amit Patel',  'faculty2@college.edu', 'faculty123', 'faculty'),
(5, 'Sneha Patil',     'student2@college.edu', 'student123', 'student');

-- Admin
INSERT IGNORE INTO admins (admin_id, user_id) VALUES (1, 1);

-- Faculty
INSERT IGNORE INTO faculties (faculty_id, user_id, dept_id) VALUES
(1, 2, 1),
(2, 4, 2);

-- Students
INSERT IGNORE INTO students (student_id, user_id, dept_id, semester, rollno) VALUES
(1, 3, 1, 3, 'CS2022001'),
(2, 5, 1, 3, 'CS2022002');

-- Subjects
INSERT IGNORE INTO subjects (sub_id, sub_name, faculty_id, dept_id, sem_id) VALUES
(1, 'Data Structures',        1, 1, 3),
(2, 'Operating Systems',      1, 1, 3),
(3, 'Database Management',    2, 1, 3),
(4, 'Computer Networks',      2, 2, 3);

-- Exams
INSERT IGNORE INTO exams (exam_id, exam_name, exam_date, total_marks, sub_id, sem_id) VALUES
(1, 'Mid Semester 1', '2026-02-10', 50, 1, 3),
(2, 'Mid Semester 1', '2026-02-12', 50, 2, 3),
(3, 'Mid Semester 1', '2026-02-14', 50, 3, 3),
(4, 'End Semester',   '2026-04-20', 100, 1, 3),
(5, 'End Semester',   '2026-04-22', 100, 2, 3);

-- Attendance (student 1)
INSERT IGNORE INTO attendance (student_id, subject_id, date, status, time) VALUES
(1,1,'2026-01-06','present','09:00'),
(1,1,'2026-01-08','present','09:00'),
(1,1,'2026-01-10','absent', '09:00'),
(1,1,'2026-01-13','present','09:00'),
(1,1,'2026-01-15','present','09:00'),
(1,1,'2026-01-17','absent', '09:00'),
(1,1,'2026-01-20','present','09:00'),
(1,1,'2026-01-22','present','09:00'),
(1,2,'2026-01-07','present','10:00'),
(1,2,'2026-01-09','present','10:00'),
(1,2,'2026-01-11','absent', '10:00'),
(1,2,'2026-01-14','present','10:00'),
(1,2,'2026-01-16','present','10:00'),
(1,2,'2026-01-18','present','10:00'),
(1,3,'2026-01-06','absent', '11:00'),
(1,3,'2026-01-08','present','11:00'),
(1,3,'2026-01-10','present','11:00'),
(1,3,'2026-01-13','absent', '11:00'),
(1,3,'2026-01-15','present','11:00'),
(1,4,'2026-01-07','present','12:00'),
(1,4,'2026-01-09','absent', '12:00'),
(1,4,'2026-01-11','present','12:00'),
(1,4,'2026-01-14','present','12:00');

-- Attendance (student 2)
INSERT IGNORE INTO attendance (student_id, subject_id, date, status, time) VALUES
(2,1,'2026-01-06','present','09:00'),
(2,1,'2026-01-08','absent', '09:00'),
(2,1,'2026-01-10','present','09:00'),
(2,2,'2026-01-07','present','10:00'),
(2,2,'2026-01-09','present','10:00'),
(2,3,'2026-01-06','absent', '11:00'),
(2,3,'2026-01-08','absent', '11:00');

-- Marks (student 1)
INSERT IGNORE INTO marks (student_id, subject_id, marks_obtained, total_marks, subject, exam_id) VALUES
(1, 1, 38, 50, 'Data Structures',     1),
(1, 2, 42, 50, 'Operating Systems',   2),
(1, 3, 35, 50, 'Database Management', 3),
(1, 1, 72, 100,'Data Structures',     4),
(1, 2, 81, 100,'Operating Systems',   5);

-- Marks (student 2)
INSERT IGNORE INTO marks (student_id, subject_id, marks_obtained, total_marks, subject, exam_id) VALUES
(2, 1, 45, 50, 'Data Structures',   1),
(2, 2, 30, 50, 'Operating Systems', 2);

-- Notices
INSERT IGNORE INTO notices (sender_role, message, date) VALUES
('admin',   'All students must submit their project proposals by April 15th.', '2026-03-28'),
('faculty', 'Mid semester results have been uploaded. Check your marks section.', '2026-03-25'),
('admin',   'College will remain closed on April 10th for a public holiday.', '2026-03-20');

SELECT 'Database setup complete!' AS Status;
