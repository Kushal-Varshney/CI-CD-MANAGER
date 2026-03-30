from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.String, primary_key=True)
    password = db.Column(db.String)
    github_token = db.Column(db.String, nullable=True)
    gemini_api_key = db.Column(db.String, nullable=True)

class PipelineRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('user.id'), nullable=False)
    source = db.Column(db.String, nullable=False) # 'github' or 'file'
    name = db.Column(db.String, nullable=False)   # repo name or file name
    total_time = db.Column(db.Integer, default=0)
    score = db.Column(db.Integer, default=0)
    dep_analysis = db.Column(db.String, nullable=True)
    metrics_payload = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    steps = db.relationship('PipelineStep', backref='run', lazy=True, cascade="all, delete-orphan")

class PipelineStep(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('pipeline_run.id'), nullable=False)
    step_name = db.Column(db.String, nullable=False)
    time = db.Column(db.Integer, default=0)
    status = db.Column(db.String, nullable=False)
