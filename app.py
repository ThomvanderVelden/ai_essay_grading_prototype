from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import time
import random
import json
import tempfile
from datetime import datetime
import uuid

# Add this near the top of your file
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Import the LLM grader utility
from utils.llm_grader import grade_essay_with_llm, analyze_essay_overview

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # In production, use a proper secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///essay_grading.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['TEMPLATES_AUTO_RELOAD'] = True  # Ensure templates aren't cached

# Configure session to use filesystem instead of cookies when size exceeds limit
SESSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flask_sessions')
if not os.path.exists(SESSION_DIR):
    os.makedirs(SESSION_DIR)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = SESSION_DIR
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True

# Initialize Flask-Session
from flask_session import Session
sess = Session(app)

# Configure file uploads
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max upload
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

# Configure temporary essay text storage
TEMP_ESSAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp_essays')
if not os.path.exists(TEMP_ESSAY_DIR):
    os.makedirs(TEMP_ESSAY_DIR)

# Initialize SQLAlchemy
db = SQLAlchemy(app)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Association table for many-to-many relationship between Student and Class
student_class = db.Table('student_class',
    db.Column('student_id', db.Integer, db.ForeignKey('student.id'), primary_key=True),
    db.Column('class_id', db.Integer, db.ForeignKey('class.id'), primary_key=True)
)

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    rubrics = db.relationship('Rubric', backref='user', lazy=True)
    essays = db.relationship('Essay', backref='user', lazy=True)
    classes = db.relationship('Class', backref='teacher', lazy=True)
    students = db.relationship('Student', backref='teacher', lazy=True)

class Rubric(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    criteria = db.relationship('RubricCriterion', backref='rubric', lazy=True, cascade="all, delete-orphan")
    essays = db.relationship('Essay', backref='rubric', lazy=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Classes using this rubric
    classes = db.relationship('Class', backref='assigned_rubric', lazy=True)

class RubricCriterion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    max_points = db.Column(db.Integer, nullable=False)
    rubric_id = db.Column(db.Integer, db.ForeignKey('rubric.id'), nullable=False)
    grades = db.relationship('Grade', backref='criterion_ref', lazy=True)
    # Field to store point descriptions as JSON
    point_descriptions = db.Column(db.Text, nullable=True)
    
    def get_point_descriptions(self):
        """Return point descriptions as a dictionary"""
        if self.point_descriptions:
            return json.loads(self.point_descriptions)
        return {}
    
    def set_point_descriptions(self, descriptions):
        """Set point descriptions from a dictionary"""
        self.point_descriptions = json.dumps(descriptions)

class Class(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rubric_id = db.Column(db.Integer, db.ForeignKey('rubric.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Many-to-many relationship with Student through student_class
    students = db.relationship('Student', secondary=student_class, lazy='subquery',
                               backref=db.backref('classes', lazy=True))
    # Essays for this class
    essays = db.relationship('Essay', backref='class_ref', lazy=True)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Teacher
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Essays by this student
    essays = db.relationship('Essay', backref='student', lazy=True)

class Essay(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_name = db.Column(db.String(100), nullable=False)
    rubric_id = db.Column(db.Integer, db.ForeignKey('rubric.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_points = db.Column(db.Integer, nullable=False)
    max_points = db.Column(db.Integer, nullable=False)
    percentage = db.Column(db.Integer, nullable=False)
    overall_feedback = db.Column(db.Text, nullable=False)
    grades = db.relationship('Grade', backref='essay', lazy=True, cascade="all, delete-orphan")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Fields for class and student
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=True)

class Grade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    essay_id = db.Column(db.Integer, db.ForeignKey('essay.id'), nullable=False)
    criterion_id = db.Column(db.Integer, db.ForeignKey('rubric_criterion.id'), nullable=False)
    criterion_name = db.Column(db.String(100), nullable=False)
    points = db.Column(db.Integer, nullable=False)
    max_points = db.Column(db.Integer, nullable=False)
    feedback = db.Column(db.Text, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Helper functions for file uploads
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_essay_file(file, user_id):
    """Save the uploaded essay file and return the file path"""
    # Create user directory if it doesn't exist
    user_dir = os.path.join(app.config['UPLOAD_FOLDER'], f'user_{user_id}')
    if not os.path.exists(user_dir):
        os.makedirs(user_dir)
    
    # Secure the filename and add a unique identifier
    filename = secure_filename(file.filename)
    file_id = str(uuid.uuid4())
    extension = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'txt'
    unique_filename = f"{file_id}.{extension}"
    
    # Save the file
    file_path = os.path.join(user_dir, unique_filename)
    file.save(file_path)
    
    return file_path

def extract_text_from_file(file_path):
    """Extract text from uploaded file"""
    extension = file_path.rsplit('.', 1)[1].lower()
    
    if extension == 'txt':
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    elif extension == 'pdf':
        try:
            import PyPDF2
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page_num in range(len(pdf_reader.pages)):
                    text += pdf_reader.pages[page_num].extract_text()
                return text
        except Exception as e:
            print(f"Error extracting PDF text: {e}")
            return "Error extracting text from PDF. Please try uploading a text file or pasting the content directly."
    elif extension in ['doc', 'docx']:
        try:
            import textract
            text = textract.process(file_path).decode('utf-8')
            return text
        except Exception as e:
            print(f"Error extracting Word document text: {e}")
            return "Error extracting text from Word document. Please try uploading a text file or pasting the content directly."
    else:
        return "Unsupported file format. Please upload a .txt, .pdf, .doc or .docx file."

def save_essay_text_to_temp_file(essay_text):
    """Save essay text to a temporary file and return the file path"""
    file_id = str(uuid.uuid4())
    file_path = os.path.join(TEMP_ESSAY_DIR, f"{file_id}.txt")
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(essay_text)
    
    return file_path

def read_essay_text_from_temp_file(file_path):
    """Read essay text from a temporary file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()

def delete_temp_file(file_path):
    """Delete a temporary file if it exists"""
    if os.path.exists(file_path):
        os.remove(file_path)

# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials!', 'danger')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        user_exists = User.query.filter_by(email=email).first()
        
        if user_exists:
            flash('Email address already registered!', 'danger')
            return redirect(url_for('register'))
            
        if password != confirm_password:
            flash('Passwords do not match!', 'danger')
            return redirect(url_for('register'))
            
        new_user = User(
            name=name,
            email=email,
            password=generate_password_hash(password)
        )
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('Account created successfully! You can now login.', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out!', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user_rubrics = Rubric.query.filter_by(user_id=current_user.id).all()
    user_essays = Essay.query.filter_by(user_id=current_user.id).order_by(Essay.created_at.desc()).all()
    user_classes = Class.query.filter_by(user_id=current_user.id).all()
    user_students = Student.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard.html', 
                          rubrics=user_rubrics, 
                          graded_essays=user_essays,
                          classes=user_classes,
                          students_count=len(user_students))

# Class Management Routes
@app.route('/classes')
@login_required
def view_classes():
    classes = Class.query.filter_by(user_id=current_user.id).all()
    return render_template('classes/index.html', classes=classes)

@app.route('/classes/create', methods=['GET', 'POST'])
@login_required
def create_class():
    rubrics = Rubric.query.filter_by(user_id=current_user.id).all()
    
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        rubric_id = request.form.get('rubric_id')
        
        # Validate if the rubric belongs to the current user if selected
        if rubric_id:
            rubric = Rubric.query.filter_by(id=rubric_id, user_id=current_user.id).first()
            if not rubric:
                flash('Invalid rubric selection', 'danger')
                return redirect(url_for('create_class'))
        
        new_class = Class(
            name=name,
            description=description,
            user_id=current_user.id,
            rubric_id=rubric_id if rubric_id else None
        )
        
        db.session.add(new_class)
        db.session.commit()
        
        flash('Class created successfully!', 'success')
        return redirect(url_for('view_classes'))
    
    return render_template('classes/create.html', rubrics=rubrics)

@app.route('/classes/<int:class_id>')
@login_required
def view_class(class_id):
    class_obj = Class.query.filter_by(id=class_id, user_id=current_user.id).first_or_404()
    students = class_obj.students
    essays = Essay.query.filter_by(class_id=class_id).all()
    
    # Group essays by student
    essays_by_student = {}
    for essay in essays:
        if essay.student_id not in essays_by_student:
            essays_by_student[essay.student_id] = []
        essays_by_student[essay.student_id].append(essay)
    
    return render_template('classes/view.html', 
                          class_obj=class_obj, 
                          students=students, 
                          essays_by_student=essays_by_student)

@app.route('/classes/<int:class_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_class(class_id):
    class_obj = Class.query.filter_by(id=class_id, user_id=current_user.id).first_or_404()
    rubrics = Rubric.query.filter_by(user_id=current_user.id).all()
    
    if request.method == 'POST':
        class_obj.name = request.form['name']
        class_obj.description = request.form['description']
        rubric_id = request.form.get('rubric_id')
        
        # Validate if the rubric belongs to the current user if selected
        if rubric_id:
            rubric = Rubric.query.filter_by(id=rubric_id, user_id=current_user.id).first()
            if not rubric:
                flash('Invalid rubric selection', 'danger')
                return redirect(url_for('edit_class', class_id=class_id))
        
        class_obj.rubric_id = rubric_id if rubric_id else None
        
        db.session.commit()
        
        flash('Class updated successfully!', 'success')
        return redirect(url_for('view_class', class_id=class_id))
    
    return render_template('classes/edit.html', class_obj=class_obj, rubrics=rubrics)

@app.route('/classes/<int:class_id>/students', methods=['GET', 'POST'])
@login_required
def manage_class_students(class_id):
    class_obj = Class.query.filter_by(id=class_id, user_id=current_user.id).first_or_404()
    all_students = Student.query.filter_by(user_id=current_user.id).all()
    
    if request.method == 'POST':
        # Get selected student IDs from form
        selected_student_ids = request.form.getlist('student_ids')
        
        # Convert to integers
        selected_student_ids = [int(id) for id in selected_student_ids]
        
        # Update students in class
        class_obj.students = []
        for student_id in selected_student_ids:
            student = Student.query.filter_by(id=student_id, user_id=current_user.id).first()
            if student:
                class_obj.students.append(student)
        
        db.session.commit()
        
        flash('Class students updated successfully!', 'success')
        return redirect(url_for('view_class', class_id=class_id))
    
    return render_template('classes/manage_students.html', 
                          class_obj=class_obj, 
                          all_students=all_students)

# Student Management Routes
@app.route('/students')
@login_required
def view_students():
    students = Student.query.filter_by(user_id=current_user.id).all()
    return render_template('students/index.html', students=students)

@app.route('/students/create', methods=['GET', 'POST'])
@login_required
def create_student():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        
        new_student = Student(
            name=name,
            email=email,
            user_id=current_user.id
        )
        
        db.session.add(new_student)
        db.session.commit()
        
        flash('Student created successfully!', 'success')
        return redirect(url_for('view_students'))
    
    return render_template('students/create.html')

@app.route('/students/bulk_create', methods=['GET', 'POST'])
@login_required
def bulk_create_students():
    if request.method == 'POST':
        student_data = request.form['student_data']
        
        # Split by newline
        lines = student_data.strip().split('\n')
        added_count = 0
        
        for line in lines:
            if ',' in line:
                # CSV format: name, email
                name, email = line.split(',', 1)
                name = name.strip()
                email = email.strip()
            else:
                # Just name
                name = line.strip()
                email = None
            
            if name:
                new_student = Student(
                    name=name,
                    email=email,
                    user_id=current_user.id
                )
                
                db.session.add(new_student)
                added_count += 1
        
        db.session.commit()
        
        flash(f'{added_count} students added successfully!', 'success')
        return redirect(url_for('view_students'))
    
    return render_template('students/bulk_create.html')

@app.route('/students/<int:student_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_student(student_id):
    student = Student.query.filter_by(id=student_id, user_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        student.name = request.form['name']
        student.email = request.form['email']
        
        db.session.commit()
        
        flash('Student updated successfully!', 'success')
        return redirect(url_for('view_students'))
    
    return render_template('students/edit.html', student=student)

@app.route('/students/<int:student_id>')
@login_required
def view_student(student_id):
    student = Student.query.filter_by(id=student_id, user_id=current_user.id).first_or_404()
    essays = Essay.query.filter_by(student_id=student_id).order_by(Essay.created_at.desc()).all()
    
    return render_template('students/view.html', student=student, essays=essays)

# Class Grading Routes
@app.route('/classes/<int:class_id>/grade')
@login_required
def class_grading(class_id):
    class_obj = Class.query.filter_by(id=class_id, user_id=current_user.id).first_or_404()
    
    # Make sure class has a rubric assigned
    if not class_obj.rubric_id:
        flash('You need to assign a rubric to this class before grading essays.', 'warning')
        return redirect(url_for('edit_class', class_id=class_id))
    
    # Get students in this class
    students = class_obj.students
    
    # Get essays already graded for this class
    essays = Essay.query.filter_by(class_id=class_id).all()
    graded_student_ids = [essay.student_id for essay in essays]
    
    # Filter students who haven't been graded yet
    ungraded_students = [s for s in students if s.id not in graded_student_ids]
    
    return render_template('classes/grade.html', 
                          class_obj=class_obj, 
                          ungraded_students=ungraded_students,
                          graded_count=len(essays),
                          total_count=len(students))

@app.route('/classes/<int:class_id>/grade/<int:student_id>', methods=['GET', 'POST'])
@login_required
def grade_student_essay(class_id, student_id):
    class_obj = Class.query.filter_by(id=class_id, user_id=current_user.id).first_or_404()
    student = Student.query.filter_by(id=student_id, user_id=current_user.id).first_or_404()
    
    # Check if the student belongs to the class
    if student not in class_obj.students:
        flash('This student is not enrolled in this class.', 'danger')
        return redirect(url_for('class_grading', class_id=class_id))
    
    # Check if the class has a rubric
    if not class_obj.rubric_id:
        flash('You need to assign a rubric to this class before grading essays.', 'warning')
        return redirect(url_for('edit_class', class_id=class_id))
    
    if request.method == 'POST':
        # Get essay text either from file or direct input
        essay_text = ""
        file_path = None
        
        if 'essay' in request.files and request.files['essay'].filename:
            file = request.files['essay']
            if file and allowed_file(file.filename):
                try:
                    file_path = save_essay_file(file, current_user.id)
                    essay_text = extract_text_from_file(file_path)
                except Exception as e:
                    flash(f'Error processing file: {str(e)}', 'danger')
                    return redirect(request.url)
            else:
                flash('Invalid file type. Please upload a .txt, .pdf, .doc or .docx file.', 'danger')
                return redirect(request.url)
        elif request.form.get('essay_text'):
            essay_text = request.form.get('essay_text')
        else:
            flash('Please either upload a file or paste essay text.', 'danger')
            return redirect(request.url)
        
        # Always save essay text to a temporary file to avoid session size issues
        temp_text_path = save_essay_text_to_temp_file(essay_text)
        logger.debug(f"Saved essay to temporary file: {temp_text_path}")
        
        # Store minimal data in session
        session['essay_data'] = {
            'rubric_id': class_obj.rubric_id,
            'student_name': student.name,
            'student_id': student.id,
            'class_id': class_id,
            'temp_text_path': temp_text_path,
            'file_path': file_path
        }
        
        logger.debug(f"Essay data stored in session: {session['essay_data']}")
        
        # Go to processing page
        return redirect(url_for('processing_class_essay', class_id=class_id, student_id=student_id))
    
    return render_template('classes/grade_student.html', class_obj=class_obj, student=student)

@app.route('/classes/<int:class_id>/grade/<int:student_id>/processing')
@login_required
def processing_class_essay(class_id, student_id):
    logger.debug(f"Processing class essay for class {class_id} and student {student_id}")
    essay_data = session.get('essay_data')
    logger.debug(f"Session data (processing): {essay_data}")
    
    class_obj = Class.query.filter_by(id=class_id, user_id=current_user.id).first_or_404()
    student = Student.query.filter_by(id=student_id, user_id=current_user.id).first_or_404()
    
    return render_template('processing.html', 
                          class_id=class_id, 
                          student_id=student_id, 
                          student_name=student.name)

@app.route('/generate_class_results')
@login_required
def generate_class_results():
    class_id = request.args.get('class_id', type=int)
    student_id = request.args.get('student_id', type=int)
    
    logger.debug(f"Generating class results for class {class_id} and student {student_id}")
    
    class_obj = Class.query.filter_by(id=class_id, user_id=current_user.id).first_or_404()
    student = Student.query.filter_by(id=student_id, user_id=current_user.id).first_or_404()
    
    # Get essay data from session
    essay_data = session.get('essay_data')
    logger.debug(f"Essay data from session (results): {essay_data}")
    
    if not essay_data:
        flash('No essay data found. Please upload an essay.', 'danger')
        return redirect(url_for('grade_student_essay', class_id=class_id, student_id=student_id))
    
    # Use the class's assigned rubric
    rubric_id = class_obj.rubric_id
    rubric = Rubric.query.get_or_404(rubric_id)
    
    # Get essay text from temporary file
    essay_text = ""
    temp_text_path = essay_data.get('temp_text_path')
    if temp_text_path and os.path.exists(temp_text_path):
        try:
            essay_text = read_essay_text_from_temp_file(temp_text_path)
            logger.debug(f"Read essay from temp file: {temp_text_path[:30]}...")
        except Exception as e:
            logger.error(f"Error reading temp file: {str(e)}")
    
    if not essay_text:
        flash('Essay text could not be retrieved. Please try uploading again.', 'danger')
        return redirect(url_for('grade_student_essay', class_id=class_id, student_id=student_id))
    
    try:
        # Get criteria for the rubric
        criteria = RubricCriterion.query.filter_by(rubric_id=rubric_id).all()
        
        # Convert criteria to dictionaries with point descriptions
        criteria_dicts = []
        for c in criteria:
            criterion_dict = {
                'id': c.id,
                'name': c.name,
                'max_points': c.max_points,
                'point_descriptions': c.get_point_descriptions()
            }
            criteria_dicts.append(criterion_dict)
        
        # Grade the essay using LLM
        llm_grades = grade_essay_with_llm(essay_text, criteria_dicts)
        
        # Calculate total points
        total_points = sum(grade['points'] for grade in llm_grades)
        max_points = sum(grade['max_points'] for grade in llm_grades)
        
        # Generate overall feedback
        overall_feedback = analyze_essay_overview(essay_text, llm_grades)
        
        # Create new essay
        new_essay = Essay(
            student_name=student.name,
            rubric_id=rubric_id,
            user_id=current_user.id,
            total_points=total_points,
            max_points=max_points,
            percentage=round((total_points / max_points) * 100) if max_points > 0 else 0,
            overall_feedback=overall_feedback,
            class_id=class_id,
            student_id=student_id
        )
        
        db.session.add(new_essay)
        db.session.flush()  # Get the ID of the new essay
        
        # Create grades for each criterion based on LLM output
        for grade_data in llm_grades:
            criterion = next((c for c in criteria if c.name == grade_data['criterion_name']), None)
            
            if criterion:
                new_grade = Grade(
                    essay_id=new_essay.id,
                    criterion_id=criterion.id,
                    criterion_name=criterion.name,
                    points=grade_data['points'],
                    max_points=criterion.max_points,
                    feedback=grade_data['feedback']
                )
                
                db.session.add(new_grade)
        
        db.session.commit()
        
        # Clean up temporary file
        if temp_text_path and os.path.exists(temp_text_path):
            delete_temp_file(temp_text_path)
            
        # Clear the session data
        if 'essay_data' in session:
            session.pop('essay_data')
        
        flash('Essay graded successfully!', 'success')
        return redirect(url_for('view_class_results', class_id=class_id, essay_id=new_essay.id))
        
    except Exception as e:
        # Clean up temporary file in case of error
        if temp_text_path and os.path.exists(temp_text_path):
            delete_temp_file(temp_text_path)
            
        print(f"Error in LLM grading: {str(e)}")
        flash(f"There was an error grading the essay: {str(e)}", 'danger')
        return redirect(url_for('grade_student_essay', class_id=class_id, student_id=student_id))

@app.route('/classes/<int:class_id>/results/<int:essay_id>')
@login_required
def view_class_results(class_id, essay_id):
    class_obj = Class.query.filter_by(id=class_id, user_id=current_user.id).first_or_404()
    essay = Essay.query.filter_by(id=essay_id, user_id=current_user.id, class_id=class_id).first_or_404()
    
    return render_template('classes/results.html', class_obj=class_obj, essay=essay)

@app.route('/create_rubric', methods=['GET', 'POST'])
@login_required
def create_rubric():
    if request.method == 'POST':
        # Debug information
        print("Form data:", request.form)
        print("Form keys:", list(request.form.keys()))
        
        # Get the rubric name
        rubric_name = request.form['name']
        
        new_rubric = Rubric(
            name=rubric_name,
            user_id=current_user.id
        )
        
        db.session.add(new_rubric)
        db.session.flush()  # Get the ID of the new rubric
        
        try:
            # Get the number of criteria from the form
            criteria_count = int(request.form['criteria_count'])
        except KeyError:
            # Default to 3 if criteria_count is missing
            criteria_count = 3
            print("Warning: criteria_count not found in form, defaulting to 3")
            
        # Process each criterion
        for i in range(1, criteria_count + 1):
            criterion_name = request.form[f'criterion{i}']
            max_points = int(request.form[f'points{i}'])
            
            # Create a dictionary to store point descriptions
            point_descriptions = {}
            for point in range(1, max_points + 1):
                description_key = f'criterion{i}_point{point}'
                if description_key in request.form:
                    point_descriptions[str(point)] = request.form[description_key]
            
            new_criterion = RubricCriterion(
                name=criterion_name,
                max_points=max_points,
                rubric_id=new_rubric.id,
                point_descriptions=json.dumps(point_descriptions)
            )
            
            db.session.add(new_criterion)
        
        db.session.commit()
        flash('Rubric created successfully!', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('create_rubric.html')

@app.route('/view_rubric/<int:rubric_id>')
@login_required
def view_rubric(rubric_id):
    rubric = Rubric.query.filter_by(id=rubric_id, user_id=current_user.id).first_or_404()
    return render_template('view_rubric.html', rubric=rubric)

@app.route('/upload_essay', methods=['GET', 'POST'])
@login_required
def upload_essay():
    user_rubrics = Rubric.query.filter_by(user_id=current_user.id).all()
    
    if request.method == 'POST':
        rubric_id = int(request.form['rubric'])
        student_name = request.form['student_name']
        
        # Validate that the rubric belongs to the current user
        rubric = Rubric.query.filter_by(id=rubric_id, user_id=current_user.id).first()
        
        if not rubric:
            flash('Invalid rubric selection', 'danger')
            return redirect(url_for('upload_essay'))
        
        # Get essay text either from file or direct input
        essay_text = ""
        file_path = None
        
        if 'essay' in request.files and request.files['essay'].filename:
            file = request.files['essay']
            if file and allowed_file(file.filename):
                try:
                    file_path = save_essay_file(file, current_user.id)
                    essay_text = extract_text_from_file(file_path)
                except Exception as e:
                    flash(f'Error processing file: {str(e)}', 'danger')
                    return redirect(request.url)
            else:
                flash('Invalid file type. Please upload a .txt, .pdf, .doc or .docx file.', 'danger')
                return redirect(request.url)
        elif request.form.get('essay_text'):
            essay_text = request.form.get('essay_text')
        else:
            flash('Please either upload a file or paste essay text.', 'danger')
            return redirect(request.url)

        # Always save to temporary file to avoid session size issues
        temp_text_path = save_essay_text_to_temp_file(essay_text)
        
        # Store the data in session for processing
        session['essay_data'] = {
            'rubric_id': rubric_id,
            'student_name': student_name,
            'temp_text_path': temp_text_path,
            'file_path': file_path
        }
        
        # Go to processing page
        return redirect(url_for('processing'))
        
    return render_template('upload_essay.html', rubrics=user_rubrics)

@app.route('/processing')
@login_required
def processing():
    # Check if this is a class essay grading
    class_id = request.args.get('class_id', type=int)
    student_id = request.args.get('student_id', type=int)
    
    if class_id and student_id:
        # Class essay grading
        student = Student.query.filter_by(id=student_id, user_id=current_user.id).first()
        if student:
            return render_template('processing.html', 
                                  class_id=class_id,
                                  student_id=student_id,
                                  student_name=student.name)
    
    # Regular essay grading - get data from session
    essay_data = session.get('essay_data')
    if not essay_data:
        flash('No essay data found. Please upload an essay.', 'danger')
        return redirect(url_for('upload_essay'))
    
    rubric_id = essay_data.get('rubric_id')
    student_name = essay_data.get('student_name')
    
    # Validate that the rubric belongs to the current user
    rubric = Rubric.query.filter_by(id=rubric_id, user_id=current_user.id).first()
    
    if not rubric:
        flash('Invalid rubric selection', 'danger')
        return redirect(url_for('dashboard'))
    
    return render_template('processing.html', 
                          rubric_id=rubric_id, 
                          student_name=student_name)

@app.route('/generate_results')
@login_required
def generate_results():
    # Check if this is a class essay grading
    class_id = request.args.get('class_id', type=int)
    student_id = request.args.get('student_id', type=int)
    
    if class_id and student_id:
        return redirect(url_for('generate_class_results', class_id=class_id, student_id=student_id))
    
    # Regular essay grading logic - get data from session
    essay_data = session.get('essay_data')
    if not essay_data:
        flash('No essay data found. Please upload an essay.', 'danger')
        return redirect(url_for('upload_essay'))
    
    rubric_id = essay_data.get('rubric_id')
    student_name = essay_data.get('student_name')
    temp_text_path = essay_data.get('temp_text_path')
    
    # Get essay text from temporary file
    essay_text = ""
    if temp_text_path and os.path.exists(temp_text_path):
        try:
            essay_text = read_essay_text_from_temp_file(temp_text_path)
        except Exception as e:
            print(f"Error reading temp file: {e}")
    
    if not essay_text:
        flash('Essay text could not be retrieved. Please try uploading again.', 'danger')
        return redirect(url_for('upload_essay'))
    
    # Validate that the rubric belongs to the current user
    rubric = Rubric.query.filter_by(id=rubric_id, user_id=current_user.id).first()
    
    if not rubric:
        flash('Invalid rubric selection', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        # Get criteria for the rubric
        criteria = RubricCriterion.query.filter_by(rubric_id=rubric_id).all()
        
        # Convert criteria to dictionaries with point descriptions
        criteria_dicts = []
        for c in criteria:
            criterion_dict = {
                'id': c.id,
                'name': c.name,
                'max_points': c.max_points,
                'point_descriptions': c.get_point_descriptions()
            }
            criteria_dicts.append(criterion_dict)
        
        # Grade the essay using LLM
        llm_grades = grade_essay_with_llm(essay_text, criteria_dicts)
        
        # Calculate total points
        total_points = sum(grade['points'] for grade in llm_grades)
        max_points = sum(grade['max_points'] for grade in llm_grades)
        
        # Generate overall feedback
        overall_feedback = analyze_essay_overview(essay_text, llm_grades)
        
        # Create new essay
        new_essay = Essay(
            student_name=student_name,
            rubric_id=rubric_id,
            user_id=current_user.id,
            total_points=total_points,
            max_points=max_points,
            percentage=round((total_points / max_points) * 100) if max_points > 0 else 0,
            overall_feedback=overall_feedback
        )
        
        db.session.add(new_essay)
        db.session.flush()  # Get the ID of the new essay
        
        # Create grades for each criterion based on LLM output
        for grade_data in llm_grades:
            criterion = next((c for c in criteria if c.name == grade_data['criterion_name']), None)
            
            if criterion:
                new_grade = Grade(
                    essay_id=new_essay.id,
                    criterion_id=criterion.id,
                    criterion_name=criterion.name,
                    points=grade_data['points'],
                    max_points=criterion.max_points,
                    feedback=grade_data['feedback']
                )
                
                db.session.add(new_grade)
        
        db.session.commit()
        
        # Clean up temporary file
        if temp_text_path and os.path.exists(temp_text_path):
            delete_temp_file(temp_text_path)
        
        # Clear the session data
        if 'essay_data' in session:
            session.pop('essay_data')
        
        return redirect(url_for('view_results', essay_id=new_essay.id))
        
    except Exception as e:
        # Clean up temporary file in case of error
        if temp_text_path and os.path.exists(temp_text_path):
            delete_temp_file(temp_text_path)
            
        print(f"Error in LLM grading: {str(e)}")
        flash(f"There was an error grading the essay: {str(e)}", 'danger')
        return redirect(url_for('upload_essay'))

@app.route('/results/<int:essay_id>')
@login_required
def view_results(essay_id):
    # Ensure the essay belongs to the current user
    essay = Essay.query.filter_by(id=essay_id, user_id=current_user.id).first_or_404()
    grades = Grade.query.filter_by(essay_id=essay_id).all()
    
    return render_template('view_results.html', essay=essay)

@app.route('/modify_grade/<int:essay_id>', methods=['POST'])
@login_required
def modify_grade(essay_id):
    # Ensure the essay belongs to the current user
    essay = Essay.query.filter_by(id=essay_id, user_id=current_user.id).first_or_404()
    grades = Grade.query.filter_by(essay_id=essay_id).all()
    
    # Update the grades based on form data
    total_points = 0
    
    for i, grade in enumerate(grades):
        points = int(request.form[f'points{i}'])
        feedback = request.form[f'feedback{i}']
        
        grade.points = points
        grade.feedback = feedback
        total_points += points
    
    # Update essay with new total score
    essay.total_points = total_points
    essay.percentage = round((total_points / essay.max_points) * 100)
    essay.overall_feedback = request.form['overall_feedback']
    
    db.session.commit()
    
    flash('Grades updated successfully!', 'success')
    return redirect(url_for('view_results', essay_id=essay_id))

# Create database tables
with app.app_context():
    db.create_all()
    
    # Create upload directories if they don't exist
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    
    # Add demo user if not exists
    demo_user = User.query.filter_by(email='teacher@example.com').first()
    if not demo_user:
        demo_user = User(
            name='John Smith',
            email='teacher@example.com',
            password=generate_password_hash('password123')
        )
        db.session.add(demo_user)
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)
