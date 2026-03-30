from flask import Flask, render_template, request, redirect, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import os

from models import db, User, PipelineRun, PipelineStep
from parser import parse_logs
from analysis import analyze_pipeline, generate_ai_suggestions, generate_dependency_scan, generate_ai_fix, estimate_ci_cost, analyze_security_patterns, predict_failures
from github_api import fetch_github_runs, fetch_repository_dependencies, fetch_dora_metrics, fetch_docs_freshness, fetch_flaky_tests, fetch_security_scan

app = Flask(__name__)
app.secret_key = "secret123"

# Setup SQLite Database
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'ci_analyzer.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# ---------------- LOGIN SYSTEM ----------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

users_dict = {"admin": {"password": "1234"}}

@login_manager.user_loader
def load_user(user_id):
    user = User.query.get(user_id)
    if user:
        return user
    return None

@app.route('/login', methods=['GET','POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.get(username)
        
        if user and user.password == password:
            login_user(user)
            return redirect('/')
        elif username in users_dict and users_dict[username]['password'] == password:
            if not user:
                user = User(id=username, password=password)
                db.session.add(user)
                db.session.commit()
            login_user(user)
            return redirect('/')
        else:
            error = "Invalid username or password"

    return render_template('login.html', error=error)

@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.get(username)
        if user:
            error = "Username already exists. Please choose another."
        else:
            new_user = User(id=username, password=password)
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            return redirect('/')
            
    return render_template('register.html', error=error)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/login')

# ---------------- ROUTES ----------------
@app.route('/', methods=['GET','POST'])
@login_required
def index():
    steps = []
    source = None
    name = None
    dep_text = None
    dora_avg = None
    docs_drift = None
    flaky_tests = []
    security_yaml = None
    github_error = None

    # FILE UPLOAD
    if request.method == 'POST' and 'logfile' in request.files:
        file = request.files['logfile']
        if file and file.filename != '':
            content = file.read().decode('utf-8')
            steps = parse_logs(content)
            source = 'file'
            name = file.filename

    # GITHUB INPUT
    elif request.method == 'POST' and request.form.get('repo'):
        repo = request.form['repo']
        token = request.form.get('token', '')

        # Use saved GitHub token if available and token is blank
        if not token and current_user.github_token:
            token = current_user.github_token

        steps, github_error = fetch_github_runs(repo, token)
        dep_text = fetch_repository_dependencies(repo, token)
        dora_avg = fetch_dora_metrics(repo, token)
        docs_drift = fetch_docs_freshness(repo, token)
        flaky_tests = fetch_flaky_tests(repo, token)
        security_yaml = fetch_security_scan(repo, token)
        source = 'github'
        name = repo

    if not steps:
        runs = PipelineRun.query.filter_by(user_id=current_user.id).order_by(PipelineRun.timestamp.desc()).limit(10).all()
        
        trend_labels = []
        trend_scores = []
        if runs and len(runs) > 1:
            for r in reversed(runs):
                trend_labels.append(r.timestamp.strftime('%m-%d %H:%M'))
                trend_scores.append(r.score)

        return render_template(
            'index.html',
            steps=None,
            total=0,
            slowest={"step": "-", "time": 0, "percent": 0},
            failed=[],
            suggestions=[],
            score=0,
            score_explain=[],
            history=runs,
            trend_labels=trend_labels,
            trend_scores=trend_scores,
            previous_time=None,
            improvement=None,
            failure_analysis=[],
            ai_suggestions=[],
            dep_analysis=[],
            metrics=None,
            run_id=None,
            flaky_tests=[],
            security_findings=[],
            cost_estimate=None,
            failure_predictions=[],
            error=github_error
        )

    total, slowest, failed, suggestions, score, score_explain = analyze_pipeline(steps)
    
    # Generate Advanced AI insights dynamically
    ai_suggestions = generate_ai_suggestions(steps, getattr(current_user, 'gemini_api_key', None))
    
    dep_analysis = generate_dependency_scan(dep_text, getattr(current_user, 'gemini_api_key', None))
    auto_fix_yaml = generate_ai_fix(steps, dep_analysis, getattr(current_user, 'gemini_api_key', None)) if source == 'github' else None

    # Construct unified metrics payload
    cost_estimate = estimate_ci_cost(steps)
    security_findings = analyze_security_patterns(security_yaml) if source == 'github' else []
    flaky_data = flaky_tests if source == 'github' else []
    
    metrics_payload = {
        "dora_avg": dora_avg if source == 'github' else None,
        "docs_drift": docs_drift if source == 'github' else None,
        "auto_fix_yaml": auto_fix_yaml
    }

    previous_time = None
    improvement = None
    failure_analysis = []

    # Get past runs for comparison
    if source and name:
        past_runs = PipelineRun.query.filter_by(user_id=current_user.id, name=name).order_by(PipelineRun.timestamp.desc()).limit(5).all()
        if past_runs:
            previous_run = past_runs[0]
            previous_time = previous_run.total_time
            if previous_time > 0:
                calc = ((total - previous_time) / previous_time) * 100
                improvement = int(calc)
            
            if failed:
                run_ids = [r.id for r in past_runs]
                past_steps = PipelineStep.query.filter(PipelineStep.run_id.in_(run_ids), PipelineStep.status=='FAILED').all()
                failed_counts = {}
                for ps in past_steps:
                    failed_counts[ps.step_name] = failed_counts.get(ps.step_name, 0) + 1
                
                for f in failed:
                    count = failed_counts.get(f['step'], 0) + 1 # +1 for current run
                    if count > 1:
                        failure_analysis.append(f"{f['step']} failed {count} times in last {len(past_runs) + 1} runs")

    # Save to Database
    if source and name:
        import json
        new_run = PipelineRun(
            user_id=current_user.id,
            source=source,
            name=name,
            total_time=total,
            score=score,
            dep_analysis=json.dumps(dep_analysis),
            metrics_payload=json.dumps(metrics_payload)
        )
        db.session.add(new_run)
        db.session.flush()

        for s in steps:
            db.session.add(PipelineStep(
                run_id=new_run.id,
                step_name=s['step'],
                time=s['time'],
                status=s['status']
            ))
        db.session.commit()

    runs = PipelineRun.query.filter_by(user_id=current_user.id).order_by(PipelineRun.timestamp.desc()).limit(10).all()

    trend_labels = []
    trend_scores = []
    if runs and len(runs) > 1:
        for r in reversed(runs):
            trend_labels.append(r.timestamp.strftime('%m-%d %H:%M'))
            trend_scores.append(r.score)

    return render_template(
        'index.html',
        steps=steps,
        total=total,
        slowest=slowest,
        failed=failed,
        suggestions=suggestions,
        score=score,
        score_explain=score_explain,
        history=runs,
        trend_labels=trend_labels,
        trend_scores=trend_scores,
        previous_time=previous_time,
        improvement=improvement,
        failure_analysis=failure_analysis,
        ai_suggestions=ai_suggestions,
        dep_analysis=dep_analysis,
        metrics=metrics_payload,
        run_id=new_run.id if source and name else None,
        flaky_tests=flaky_data,
        security_findings=security_findings,
        cost_estimate=cost_estimate,
        failure_predictions=[],
        error=None
    )

@app.route('/download-fix/<int:run_id>')
@login_required
def download_fix(run_id):
    from flask import Response
    import json
    run = PipelineRun.query.filter_by(id=run_id, user_id=current_user.id).first_or_404()
    if run.metrics_payload:
        data = json.loads(run.metrics_payload)
        yaml_content = data.get('auto_fix_yaml')
        if yaml_content:
            return Response(yaml_content, mimetype='text/yaml', headers={"Content-disposition":"attachment; filename=optimized-workflow.yml"})
    return "Optimized pipeline file unavailable.", 404

@app.route('/history')
@login_required
def history():
    limit = request.args.get('limit', type=int)
    project_name = request.args.get('project', type=str)
    
    query = PipelineRun.query.filter_by(user_id=current_user.id)
    if project_name:
        query = query.filter_by(name=project_name)
    
    query = query.order_by(PipelineRun.timestamp.desc())
    
    if limit:
        runs = query.limit(limit).all()
    else:
        runs = query.all()
        
    return render_template('history.html', runs=runs, current_limit=limit, project_name=project_name)

@app.route('/projects')
@login_required
def projects():
    all_runs = PipelineRun.query.filter_by(user_id=current_user.id).order_by(PipelineRun.timestamp.desc()).all()
    
    unique_projects = {}
    project_run_counts = {}
    
    for r in all_runs:
        if r.name not in unique_projects:
            unique_projects[r.name] = r
            project_run_counts[r.name] = 1
        else:
            project_run_counts[r.name] += 1
            
    return render_template('projects.html', projects=unique_projects.values(), counts=project_run_counts)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        current_user.github_token = request.form.get('github_token', '')
        current_user.gemini_api_key = request.form.get('gemini_api_key', '')
        db.session.commit()
        return render_template('settings.html', success="Integrations saved successfully!")
    return render_template('settings.html')

@app.route('/run/<int:run_id>')
@login_required
def view_run(run_id):
    import json
    pipeline_run = PipelineRun.query.filter_by(id=run_id, user_id=current_user.id).first_or_404()
    
    metrics_payload = json.loads(pipeline_run.metrics_payload) if getattr(pipeline_run, 'metrics_payload', None) else {}
    
    steps = []
    for step in pipeline_run.steps:
        steps.append({
            'step': step.step_name,
            'time': step.time,
            'status': step.status
        })
        
    total, slowest, failed, suggestions, score, score_explain = analyze_pipeline(steps)

    previous_time = None
    improvement = None
    failure_analysis = []

    past_runs = PipelineRun.query.filter(
        PipelineRun.user_id == current_user.id,
        PipelineRun.name == pipeline_run.name,
        PipelineRun.timestamp < pipeline_run.timestamp
    ).order_by(PipelineRun.timestamp.desc()).limit(5).all()
    
    if past_runs:
        previous_run = past_runs[0]
        previous_time = previous_run.total_time
        if previous_time > 0:
            calc = ((total - previous_time) / previous_time) * 100
            improvement = int(calc)
        
        if failed:
            run_ids = [r.id for r in past_runs]
            past_steps = PipelineStep.query.filter(PipelineStep.run_id.in_(run_ids), PipelineStep.status=='FAILED').all()
            failed_counts = {}
            for ps in past_steps:
                failed_counts[ps.step_name] = failed_counts.get(ps.step_name, 0) + 1
            
            for f in failed:
                count = failed_counts.get(f['step'], 0) + 1 
                if count > 1:
                    failure_analysis.append(f"{f['step']} failed {count} times in last {len(past_runs) + 1} runs")

    runs_for_trend = PipelineRun.query.filter_by(user_id=current_user.id, name=pipeline_run.name).order_by(PipelineRun.timestamp.desc()).limit(10).all()
    trend_labels = []
    trend_scores = []
    if runs_for_trend and len(runs_for_trend) > 1:
        for r in reversed(runs_for_trend):
            trend_labels.append(r.timestamp.strftime('%m-%d %H:%M'))
            trend_scores.append(r.score)

    return render_template(
        'index.html',
        steps=steps,
        total=total,
        slowest=slowest,
        failed=failed,
        suggestions=suggestions,
        score=score,
        score_explain=score_explain,
        history=runs_for_trend,
        trend_labels=trend_labels,
        trend_scores=trend_scores,
        previous_time=previous_time,
        improvement=improvement,
        failure_analysis=failure_analysis,
        is_historical=True,
        target_name=pipeline_run.name,
        run_date=pipeline_run.timestamp.strftime('%Y-%m-%d %H:%M'),
        ai_suggestions=[],
        dep_analysis=json.loads(pipeline_run.dep_analysis) if getattr(pipeline_run, 'dep_analysis', None) else [],
        metrics=metrics_payload,
        run_id=pipeline_run.id,
        flaky_tests=[],
        security_findings=[],
        cost_estimate=estimate_ci_cost(steps),
        failure_predictions=predict_failures(
            PipelineStep.query.filter(
                PipelineStep.run_id.in_(
                    [r.id for r in PipelineRun.query.filter_by(user_id=current_user.id, name=pipeline_run.name).limit(10).all()]
                )
            ).all()
        )
    )

# ============================================================
# ============= NEW MODULE PAGES =============================
# ============================================================

@app.route('/coverage-scan', methods=['GET', 'POST'])
@login_required
def coverage_scan():
    from analysis import analyze_coverage_gaps
    result = None
    repo = None
    scan_source = None
    error = None
    if request.method == 'POST':
        repo = request.form.get('repo', '')
        local_path = request.form.get('local_path', '').strip()
        try:
            if local_path:
                from local_scanner import scan_local_code_structure
                structure = scan_local_code_structure(local_path)
                result = analyze_coverage_gaps(structure)
                scan_source = local_path
            elif repo:
                from github_api import fetch_code_structure
                token = current_user.github_token or ''
                structure = fetch_code_structure(repo, token)
                if structure is None:
                    error = f"Could not fetch code structure for '{repo}'. This may be due to GitHub API rate limiting. Add a GitHub token in Settings to increase your API limit."
                else:
                    result = analyze_coverage_gaps(structure)
                scan_source = repo
        except Exception as e:
            error = f"Scan failed: {str(e)}"
    return render_template('coverage.html', result=result, repo=scan_source or repo, error=error)

@app.route('/pr-analysis', methods=['GET', 'POST'])
@login_required
def pr_analysis():
    from analysis import score_pr_complexity
    result = None
    repo = None
    scan_source = None
    error = None
    if request.method == 'POST':
        repo = request.form.get('repo', '')
        local_path = request.form.get('local_path', '').strip()
        try:
            if local_path:
                from local_scanner import scan_local_prs
                prs = scan_local_prs(local_path)
                result = score_pr_complexity(prs)
                scan_source = local_path
            elif repo:
                from github_api import fetch_pr_details
                token = current_user.github_token or ''
                prs = fetch_pr_details(repo, token)
                if not prs:
                    error = f"No PR data found for '{repo}'. This may be due to GitHub API rate limiting or no closed PRs exist. Add a GitHub token in Settings."
                else:
                    result = score_pr_complexity(prs)
                scan_source = repo
        except Exception as e:
            error = f"Scan failed: {str(e)}"
    return render_template('pr_analysis.html', result=result, repo=scan_source or repo, error=error)

@app.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    from analysis import score_onboarding
    result = None
    repo = None
    scan_source = None
    error = None
    if request.method == 'POST':
        repo = request.form.get('repo', '')
        local_path = request.form.get('local_path', '').strip()
        try:
            if local_path:
                from local_scanner import scan_local_onboarding
                files = scan_local_onboarding(local_path)
                result = score_onboarding(files)
                scan_source = local_path
            elif repo:
                from github_api import fetch_onboarding_files
                token = current_user.github_token or ''
                files = fetch_onboarding_files(repo, token)
                result = score_onboarding(files)
                scan_source = repo
        except Exception as e:
            error = f"Scan failed: {str(e)}"
    return render_template('onboarding.html', result=result, repo=scan_source or repo, error=error)

@app.route('/commit-patterns', methods=['GET', 'POST'])
@login_required
def commit_patterns():
    from analysis import analyze_commit_health
    result = None
    repo = None
    scan_source = None
    error = None
    if request.method == 'POST':
        repo = request.form.get('repo', '')
        local_path = request.form.get('local_path', '').strip()
        try:
            if local_path:
                from local_scanner import scan_local_commits
                commits = scan_local_commits(local_path)
                result = analyze_commit_health(commits)
                scan_source = local_path
            elif repo:
                from github_api import fetch_commit_patterns
                token = current_user.github_token or ''
                commits = fetch_commit_patterns(repo, token)
                if not commits:
                    error = f"No commit data found for '{repo}'. This may be due to GitHub API rate limiting. Add a GitHub token in Settings."
                else:
                    result = analyze_commit_health(commits)
                scan_source = repo
        except Exception as e:
            error = f"Scan failed: {str(e)}"
    return render_template('commit_patterns.html', result=result, repo=scan_source or repo, error=error)

@app.route('/env-check', methods=['GET', 'POST'])
@login_required
def env_check():
    from analysis import detect_env_drift
    result = None
    repo = None
    scan_source = None
    error = None
    if request.method == 'POST':
        repo = request.form.get('repo', '')
        local_path = request.form.get('local_path', '').strip()
        try:
            if local_path:
                from local_scanner import scan_local_env
                env_data = scan_local_env(local_path)
                result = detect_env_drift(env_data)
                scan_source = local_path
            elif repo:
                from github_api import fetch_env_files
                token = current_user.github_token or ''
                env_data = fetch_env_files(repo, token)
                result = detect_env_drift(env_data)
                scan_source = repo
        except Exception as e:
            error = f"Scan failed: {str(e)}"
    return render_template('env_check.html', result=result, repo=scan_source or repo, error=error)

@app.route('/repo-health', methods=['GET', 'POST'])
@login_required
def repo_health():
    from analysis import analyze_coverage_gaps, score_onboarding, analyze_commit_health, detect_env_drift, analyze_security_patterns
    
    results = None
    repo = None
    scan_source = None
    error = None
    if request.method == 'POST':
        repo = request.form.get('repo', '')
        local_path = request.form.get('local_path', '').strip()
        
        try:
            if local_path:
                from local_scanner import scan_local_code_structure, scan_local_onboarding, scan_local_commits, scan_local_env, scan_local_docs_freshness
                scan_source = local_path
                coverage = analyze_coverage_gaps(scan_local_code_structure(local_path))
                onboarding_score = score_onboarding(scan_local_onboarding(local_path))
                commit_health = analyze_commit_health(scan_local_commits(local_path))
                env_drift = detect_env_drift(scan_local_env(local_path))
                docs_result = scan_local_docs_freshness(local_path)
                docs = docs_result['score'] if docs_result else None
                security = []
                dora = None
            else:
                from github_api import fetch_code_structure, fetch_onboarding_files, fetch_commit_patterns, fetch_env_files, fetch_dora_metrics, fetch_docs_freshness, fetch_security_scan
                scan_source = repo
                token = current_user.github_token or ''
                coverage = analyze_coverage_gaps(fetch_code_structure(repo, token))
                onboarding_score = score_onboarding(fetch_onboarding_files(repo, token))
                commit_health = analyze_commit_health(fetch_commit_patterns(repo, token))
                env_drift = detect_env_drift(fetch_env_files(repo, token))
                dora = fetch_dora_metrics(repo, token)
                docs = fetch_docs_freshness(repo, token)
                security = analyze_security_patterns(fetch_security_scan(repo, token))
            
            # Calculate overall health score
            scores = []
            if onboarding_score:
                scores.append(onboarding_score['score'])
            if coverage:
                scores.append(min(coverage['test_ratio'] * 2, 100))
            if commit_health:
                bus_score = min(commit_health['bus_factor'] * 20, 100)
                scores.append(bus_score)
            
            overall = round(sum(scores) / len(scores)) if scores else 0
            grade = 'A' if overall >= 80 else 'B' if overall >= 60 else 'C' if overall >= 40 else 'D' if overall >= 20 else 'F'
            
            results = {
                'overall_score': overall,
                'grade': grade,
                'coverage': coverage,
                'onboarding': onboarding_score,
                'commit_health': commit_health,
                'env_drift': env_drift,
                'dora': dora,
                'docs_drift': docs,
                'security': security
            }
        except Exception as e:
            error = f"Full scan failed: {str(e)}. This may be due to GitHub API rate limiting. Add a GitHub token in Settings to increase your API limit."
    
    return render_template('repo_health.html', results=results, repo=scan_source or repo, error=error)

@app.route('/docs-freshness', methods=['GET', 'POST'])
@login_required
def docs_freshness():
    result = None
    repo = None
    scan_source = None
    error = None
    if request.method == 'POST':
        repo = request.form.get('repo', '')
        local_path = request.form.get('local_path', '').strip()
        try:
            if local_path:
                from local_scanner import scan_local_docs_freshness
                result = scan_local_docs_freshness(local_path)
                scan_source = local_path
            elif repo:
                from github_api import fetch_docs_freshness_full
                token = current_user.github_token or ''
                result = fetch_docs_freshness_full(repo, token)
                if result is None:
                    error = f"Could not fetch docs data for '{repo}'. This may be due to GitHub API rate limiting. Add a GitHub token in Settings."
                scan_source = repo
        except Exception as e:
            error = f"Scan failed: {str(e)}"
    return render_template('docs_freshness.html', result=result, repo=scan_source or repo, error=error)

# ---------- FOLDER BROWSER API ----------
@app.route('/api/browse-folder')
@login_required
def browse_folder():
    """Opens native macOS folder picker and returns the selected path."""
    import subprocess
    try:
        result = subprocess.run(
            ['osascript', '-e', 'POSIX path of (choose folder with prompt "Select project folder to scan")'],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip().rstrip('/')
            return jsonify({'path': path})
        return jsonify({'path': '', 'error': 'No folder selected'})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})

# ---------------- INITIALIZATION ----------------
with app.app_context():
    db.create_all()

# ---------------- RUN ----------------
if __name__ == '__main__':
    app.run(debug=True)