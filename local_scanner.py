"""
local_scanner.py — Local filesystem scanning for DX-Ray.
Mirrors github_api.py functions but operates on local project directories.
"""
import os
import subprocess
from datetime import datetime


def scan_local_code_structure(project_path):
    """Scans local directory for source vs test files. Mirrors fetch_code_structure."""
    if not os.path.isdir(project_path):
        return None

    source_exts = {'.py', '.js', '.ts', '.java', '.go', '.rb', '.rs', '.c', '.cpp', '.cs', '.php', '.swift', '.kt', '.jsx', '.tsx', '.vue', '.svelte'}
    test_keywords = {'test', 'tests', 'spec', 'specs', '__tests__', '__test__'}

    source_files = []
    test_files = []

    for root, dirs, files in os.walk(project_path):
        # Skip hidden dirs and common non-source dirs
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {'node_modules', 'venv', 'env', '__pycache__', '.git', 'dist', 'build', 'vendor'}]

        rel_root = os.path.relpath(root, project_path)
        if rel_root == '.':
            rel_root = ''

        for f in files:
            _, ext = os.path.splitext(f)
            if ext.lower() in source_exts:
                rel_path = os.path.join(rel_root, f) if rel_root else f
                # Determine if this is a test file
                is_test = (
                    any(kw in f.lower() for kw in ['test', 'spec', '_test', '.test', '.spec']) or
                    any(kw in rel_root.lower().split(os.sep) for kw in test_keywords)
                )
                if is_test:
                    test_files.append(rel_path)
                else:
                    source_files.append(rel_path)

    return {'source_files': source_files, 'test_files': test_files}


def scan_local_docs_freshness(project_path):
    """Scans local directory for doc files and their freshness. Mirrors fetch_docs_freshness_full."""
    if not os.path.isdir(project_path):
        return None

    doc_files = [
        {'path': 'README.md', 'label': 'Project README', 'weight': 25},
        {'path': 'CONTRIBUTING.md', 'label': 'Contributing Guide', 'weight': 15},
        {'path': 'CHANGELOG.md', 'label': 'Changelog', 'weight': 15},
        {'path': 'docs', 'label': 'Documentation Directory', 'weight': 15},
        {'path': 'API.md', 'label': 'API Documentation', 'weight': 10},
        {'path': 'ARCHITECTURE.md', 'label': 'Architecture Docs', 'weight': 10},
        {'path': os.path.join('.github', 'PULL_REQUEST_TEMPLATE.md'), 'label': 'PR Template', 'weight': 5},
        {'path': os.path.join('.github', 'ISSUE_TEMPLATE'), 'label': 'Issue Templates', 'weight': 5},
    ]

    # Get latest modification time across all source files
    latest_code_time = None
    source_exts = {'.py', '.js', '.ts', '.java', '.go', '.rb'}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {'node_modules', 'venv', '__pycache__', 'dist', 'build'}]
        for f in files:
            _, ext = os.path.splitext(f)
            if ext.lower() in source_exts:
                full = os.path.join(root, f)
                mtime = os.path.getmtime(full)
                if latest_code_time is None or mtime > latest_code_time:
                    latest_code_time = mtime

    if latest_code_time is None:
        latest_code_time = datetime.now().timestamp()

    code_date = datetime.fromtimestamp(latest_code_time)
    results = []
    total_score = 0
    max_score = 0

    for doc in doc_files:
        max_score += doc['weight']
        full_path = os.path.join(project_path, doc['path'])

        if os.path.exists(full_path):
            mtime = os.path.getmtime(full_path)
            doc_date = datetime.fromtimestamp(mtime)
            drift = (code_date - doc_date).days
            if drift < 0:
                drift = 0
            last_updated = doc_date.strftime('%Y-%m-%d')

            if drift <= 7:
                severity = 'FRESH'
                earned = doc['weight']
            elif drift <= 30:
                severity = 'OK'
                earned = int(doc['weight'] * 0.7)
            elif drift <= 90:
                severity = 'STALE'
                earned = int(doc['weight'] * 0.3)
            else:
                severity = 'CRITICAL'
                earned = 0

            total_score += earned
            results.append({
                'file': doc['path'], 'label': doc['label'], 'exists': True,
                'drift_days': drift, 'last_updated': last_updated,
                'severity': severity, 'points': earned, 'max_points': doc['weight']
            })
        else:
            results.append({
                'file': doc['path'], 'label': doc['label'], 'exists': False,
                'drift_days': None, 'last_updated': None,
                'severity': 'MISSING', 'points': 0, 'max_points': doc['weight']
            })

    freshness_pct = round((total_score / max_score) * 100) if max_score > 0 else 0
    grade = 'A' if freshness_pct >= 80 else 'B' if freshness_pct >= 60 else 'C' if freshness_pct >= 40 else 'D' if freshness_pct >= 20 else 'F'

    return {
        'files': results, 'score': freshness_pct, 'grade': grade,
        'total_points': total_score, 'max_points': max_score,
        'latest_code_commit': code_date.strftime('%Y-%m-%d'),
        'docs_found': sum(1 for r in results if r['exists']),
        'docs_total': len(results),
        'stale_count': sum(1 for r in results if r['severity'] in ['STALE', 'CRITICAL']),
        'missing_count': sum(1 for r in results if not r['exists']),
    }


def scan_local_onboarding(project_path):
    """Scans local directory for onboarding files. Mirrors fetch_onboarding_files."""
    if not os.path.isdir(project_path):
        return None

    files_to_check = [
        'README.md', 'CONTRIBUTING.md', 'LICENSE', '.gitignore',
        'package.json', 'setup.py', 'pyproject.toml',
        'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml',
        '.env.example', 'Makefile', 'CODE_OF_CONDUCT.md',
        '.editorconfig', 'setup.sh'
    ]

    result = {}
    for fname in files_to_check:
        result[fname] = os.path.exists(os.path.join(project_path, fname))

    # Check README for Getting Started section
    readme_path = os.path.join(project_path, 'README.md')
    has_getting_started = False
    if os.path.exists(readme_path):
        try:
            with open(readme_path, 'r', errors='ignore') as f:
                content = f.read().lower()
                has_getting_started = 'getting started' in content or 'quick start' in content or 'installation' in content
        except Exception:
            pass
    result['has_getting_started_section'] = has_getting_started

    return result


def scan_local_commits(project_path):
    """Scans local git history for commit patterns. Mirrors fetch_commit_patterns."""
    if not os.path.isdir(os.path.join(project_path, '.git')):
        return None

    try:
        result = subprocess.run(
            ['git', 'log', '--format=%H|%an|%aI', '-100'],
            cwd=project_path, capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return None

        commits = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('|', 2)
            if len(parts) < 3:
                continue
            sha, author, date_str = parts
            try:
                dt = datetime.fromisoformat(date_str)
                commits.append({
                    'sha': sha[:7],
                    'author': author,
                    'date': dt.strftime('%Y-%m-%d'),
                    'day': dt.strftime('%A'),
                    'hour': dt.hour,
                    'message': ''
                })
            except Exception:
                continue

        return commits if commits else None
    except Exception:
        return None


def scan_local_prs(project_path):
    """Scans local git for merge commits as a proxy for PRs. Mirrors fetch_pr_details."""
    if not os.path.isdir(os.path.join(project_path, '.git')):
        return None

    try:
        result = subprocess.run(
            ['git', 'log', '--merges', '--format=%H|%an|%aI|%s', '-15'],
            cwd=project_path, capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0 or not result.stdout.strip():
            # Fallback: use recent commits as PR-like entries
            result = subprocess.run(
                ['git', 'log', '--format=%H|%an|%aI|%s', '-15'],
                cwd=project_path, capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                return None

        prs = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('|', 3)
            if len(parts) < 4:
                continue
            sha, author, date_str, title = parts

            # Get diff stats for this commit
            try:
                stat = subprocess.run(
                    ['git', 'diff', '--shortstat', f'{sha}~1', sha],
                    cwd=project_path, capture_output=True, text=True, timeout=5
                )
                additions = 0
                deletions = 0
                changed_files = 0
                if stat.stdout.strip():
                    import re
                    files_m = re.search(r'(\d+) files? changed', stat.stdout)
                    ins_m = re.search(r'(\d+) insertions?', stat.stdout)
                    del_m = re.search(r'(\d+) deletions?', stat.stdout)
                    if files_m:
                        changed_files = int(files_m.group(1))
                    if ins_m:
                        additions = int(ins_m.group(1))
                    if del_m:
                        deletions = int(del_m.group(1))
            except Exception:
                additions = 0
                deletions = 0
                changed_files = 0

            prs.append({
                'title': title,
                'user': author,
                'additions': additions,
                'deletions': deletions,
                'changed_files': changed_files,
                'comments': 0,
                'merged': True,
                'hours_to_merge': None,
                'number': sha[:7]
            })

        return prs if prs else None
    except Exception:
        return None


def scan_local_env(project_path):
    """Scans local directory for environment config files. Mirrors fetch_env_files."""
    if not os.path.isdir(project_path):
        return None

    result = {}
    files_to_read = {
        'Dockerfile': 'Dockerfile',
        'docker-compose.yml': 'docker-compose.yml',
        'docker-compose.yaml': 'docker-compose.yaml',
        '.env': '.env',
        '.env.example': '.env.example',
        '.dockerignore': '.dockerignore',
    }

    for key, fname in files_to_read.items():
        full_path = os.path.join(project_path, fname)
        if os.path.exists(full_path):
            try:
                with open(full_path, 'r', errors='ignore') as f:
                    result[key] = f.read()
            except Exception:
                pass

    # Check for CI workflow files
    workflows_dir = os.path.join(project_path, '.github', 'workflows')
    if os.path.isdir(workflows_dir):
        combined = ''
        for fname in os.listdir(workflows_dir):
            if fname.endswith(('.yml', '.yaml')):
                try:
                    with open(os.path.join(workflows_dir, fname), 'r', errors='ignore') as f:
                        combined += f.read() + '\n'
                except Exception:
                    pass
        if combined:
            result['_workflows'] = combined

    return result if result else None
