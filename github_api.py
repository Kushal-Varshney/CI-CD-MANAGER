import requests
import base64
from datetime import datetime

def fetch_github_runs(repo, token):
    # Fetch workflow runs
    url = f"https://api.github.com/repos/{repo}/actions/runs"
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"

    r = requests.get(url, headers=headers)
    data = r.json()

    workflow_runs = data.get('workflow_runs', [])
    if not workflow_runs:
        return []

    # Get the latest run ID
    latest_run = workflow_runs[0]
    run_id = latest_run['id']

    # Fetch jobs for the latest run
    jobs_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    r_jobs = requests.get(jobs_url, headers=headers)
    jobs_data = r_jobs.json()

    steps = []
    for job in jobs_data.get('jobs', []):
        name = job.get('name', 'Unnamed Job')
        status_raw = job.get('conclusion') or job.get('status') or 'UNKNOWN'
        
        if status_raw == 'success':
            status = 'SUCCESS'
        elif status_raw == 'failure':
            status = 'FAILED'
        else:
            status = str(status_raw).upper()

        created_at_str = job.get('started_at')
        updated_at_str = job.get('completed_at')
        
        duration_ms = 0
        if created_at_str and updated_at_str:
            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
            updated_at = datetime.fromisoformat(updated_at_str.replace('Z', '+00:00'))
            duration_ms = int((updated_at - created_at).total_seconds())
        
        steps.append({
            'step': name,
            'status': status,
            'time': duration_ms # time is in seconds
        })

    return steps

def fetch_repository_dependencies(repo, token):
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
        
    # Try package.json
    url = f"https://api.github.com/repos/{repo}/contents/package.json"
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        data = r.json()
        if 'content' in data:
            return base64.b64decode(data['content']).decode('utf-8')
            
    # Try requirements.txt
    url = f"https://api.github.com/repos/{repo}/contents/requirements.txt"
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        data = r.json()
        if 'content' in data:
            return base64.b64decode(data['content']).decode('utf-8')
            
    return None

def fetch_dora_metrics(repo, token):
    """Calculates Lead Time to Merge (DORA) based on recent 15 merged PRs"""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
        
    url = f"https://api.github.com/repos/{repo}/pulls?state=closed&per_page=15"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return None
        
    pull_requests = r.json()
    merged_prs = [pr for pr in pull_requests if pr.get('merged_at')]
    
    if not merged_prs:
        return "No recent merged PRs found"
        
    total_hours = 0
    for pr in merged_prs:
        created = datetime.fromisoformat(pr['created_at'].replace('Z', '+00:00'))
        merged = datetime.fromisoformat(pr['merged_at'].replace('Z', '+00:00'))
        total_hours += (merged - created).total_seconds() / 3600.0
        
    avg_hours = total_hours / len(merged_prs)
    return round(avg_hours, 1)

def fetch_docs_freshness(repo, token):
    """Checks the drift between README.md and the rest of the codebase."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
        
    readme_url = f"https://api.github.com/repos/{repo}/commits?path=README.md&per_page=1"
    code_url = f"https://api.github.com/repos/{repo}/commits?per_page=1"
    
    r_readme = requests.get(readme_url, headers=headers)
    r_code = requests.get(code_url, headers=headers)
    
    if r_readme.status_code == 200 and r_code.status_code == 200:
        readme_data = r_readme.json()
        code_data = r_code.json()
        
        if readme_data and code_data:
            readme_date_str = readme_data[0]['commit']['committer']['date']
            code_date_str = code_data[0]['commit']['committer']['date']
            
            readme_date = datetime.fromisoformat(readme_date_str.replace('Z', '+00:00'))
            code_date = datetime.fromisoformat(code_date_str.replace('Z', '+00:00'))
            
            drift_days = (code_date - readme_date).days
            return drift_days
    
    return None


def fetch_docs_freshness_full(repo, token):
    """Full docs freshness scan — checks multiple doc files for staleness."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"

    doc_files = [
        {'path': 'README.md', 'label': 'Project README', 'weight': 25},
        {'path': 'CONTRIBUTING.md', 'label': 'Contributing Guide', 'weight': 15},
        {'path': 'CHANGELOG.md', 'label': 'Changelog', 'weight': 15},
        {'path': 'docs', 'label': 'Documentation Directory', 'weight': 15},
        {'path': 'API.md', 'label': 'API Documentation', 'weight': 10},
        {'path': 'ARCHITECTURE.md', 'label': 'Architecture Docs', 'weight': 10},
        {'path': '.github/PULL_REQUEST_TEMPLATE.md', 'label': 'PR Template', 'weight': 5},
        {'path': '.github/ISSUE_TEMPLATE', 'label': 'Issue Templates', 'weight': 5},
    ]

    # Get latest code commit date
    code_url = f"https://api.github.com/repos/{repo}/commits?per_page=1"
    r_code = requests.get(code_url, headers=headers)
    if r_code.status_code != 200 or not r_code.json():
        return None

    code_date_str = r_code.json()[0]['commit']['committer']['date']
    code_date = datetime.fromisoformat(code_date_str.replace('Z', '+00:00'))

    results = []
    total_score = 0
    max_score = 0

    for doc in doc_files:
        max_score += doc['weight']
        commit_url = f"https://api.github.com/repos/{repo}/commits?path={doc['path']}&per_page=1"
        r = requests.get(commit_url, headers=headers)

        if r.status_code == 200 and r.json():
            doc_date_str = r.json()[0]['commit']['committer']['date']
            doc_date = datetime.fromisoformat(doc_date_str.replace('Z', '+00:00'))
            drift = (code_date - doc_date).days
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
                'file': doc['path'],
                'label': doc['label'],
                'exists': True,
                'drift_days': drift,
                'last_updated': last_updated,
                'severity': severity,
                'points': earned,
                'max_points': doc['weight']
            })
        else:
            results.append({
                'file': doc['path'],
                'label': doc['label'],
                'exists': False,
                'drift_days': None,
                'last_updated': None,
                'severity': 'MISSING',
                'points': 0,
                'max_points': doc['weight']
            })

    freshness_pct = round((total_score / max_score) * 100) if max_score > 0 else 0
    grade = 'A' if freshness_pct >= 80 else 'B' if freshness_pct >= 60 else 'C' if freshness_pct >= 40 else 'D' if freshness_pct >= 20 else 'F'

    return {
        'files': results,
        'score': freshness_pct,
        'grade': grade,
        'total_points': total_score,
        'max_points': max_score,
        'latest_code_commit': code_date.strftime('%Y-%m-%d'),
        'docs_found': sum(1 for r in results if r['exists']),
        'docs_total': len(results),
        'stale_count': sum(1 for r in results if r['severity'] in ['STALE', 'CRITICAL']),
        'missing_count': sum(1 for r in results if not r['exists']),
    }

def fetch_flaky_tests(repo, token):
    """Analyzes last 10 workflow runs to detect flaky (intermittently failing) jobs."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    
    url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=10"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return []
    
    runs = r.json().get('workflow_runs', [])
    if len(runs) < 2:
        return []
    
    # Track pass/fail per job name across runs
    job_results = {}  # {job_name: {'pass': 0, 'fail': 0}}
    
    for run in runs[:10]:
        run_id = run['id']
        jobs_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
        r_jobs = requests.get(jobs_url, headers=headers)
        if r_jobs.status_code != 200:
            continue
        
        for job in r_jobs.json().get('jobs', []):
            name = job.get('name', 'Unknown')
            conclusion = job.get('conclusion', '')
            
            if name not in job_results:
                job_results[name] = {'pass': 0, 'fail': 0, 'total': 0}
            
            job_results[name]['total'] += 1
            if conclusion == 'success':
                job_results[name]['pass'] += 1
            elif conclusion == 'failure':
                job_results[name]['fail'] += 1
    
    # A job is "flaky" if it has BOTH passes and failures
    flaky = []
    for name, counts in job_results.items():
        if counts['pass'] > 0 and counts['fail'] > 0:
            flakiness = round((counts['fail'] / counts['total']) * 100)
            flaky.append({
                'step': name,
                'flakiness': flakiness,
                'fail_count': counts['fail'],
                'total': counts['total']
            })
    
    return sorted(flaky, key=lambda x: x['flakiness'], reverse=True)

def fetch_security_scan(repo, token):
    """Fetches workflow YAML files and scans for dangerous CI patterns."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    
    # Fetch list of workflow files
    url = f"https://api.github.com/repos/{repo}/contents/.github/workflows"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return None
    
    files = r.json()
    combined_yaml = ""
    for f in files:
        if f['name'].endswith(('.yml', '.yaml')):
            file_r = requests.get(f['download_url'], headers=headers)
            if file_r.status_code == 200:
                combined_yaml += f"\n# --- {f['name']} ---\n" + file_r.text
    
    return combined_yaml if combined_yaml else None

def fetch_code_structure(repo, token):
    """Fetches the repo file tree to identify test coverage gaps."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    
    # Get the default branch's tree recursively
    url = f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return None
    
    tree = r.json().get('tree', [])
    
    source_files = []
    test_files = []
    
    code_exts = {'.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rb', '.rs', '.cpp', '.c', '.cs'}
    
    for item in tree:
        if item['type'] != 'blob':
            continue
        path = item['path']
        ext = '.' + path.rsplit('.', 1)[-1] if '.' in path else ''
        
        if ext not in code_exts:
            continue
        
        is_test = any(marker in path.lower() for marker in ['test', 'spec', '__test__', '_test', '.test.', '.spec.'])
        
        if is_test:
            test_files.append(path)
        else:
            source_files.append(path)
    
    return {'source_files': source_files, 'test_files': test_files}

def fetch_pr_details(repo, token):
    """Fetches last 20 PRs with full detail for complexity scoring."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    
    url = f"https://api.github.com/repos/{repo}/pulls?state=closed&per_page=20"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return []
    
    prs = r.json()
    detailed = []
    
    for pr in prs:
        pr_num = pr['number']
        # Fetch individual PR for additions/deletions/changed_files
        detail_url = f"https://api.github.com/repos/{repo}/pulls/{pr_num}"
        dr = requests.get(detail_url, headers=headers)
        if dr.status_code != 200:
            continue
        
        d = dr.json()
        
        created = datetime.fromisoformat(d['created_at'].replace('Z', '+00:00'))
        merged = None
        hours_to_merge = None
        if d.get('merged_at'):
            merged = datetime.fromisoformat(d['merged_at'].replace('Z', '+00:00'))
            hours_to_merge = round((merged - created).total_seconds() / 3600, 1)
        
        detailed.append({
            'number': pr_num,
            'title': d.get('title', ''),
            'additions': d.get('additions', 0),
            'deletions': d.get('deletions', 0),
            'changed_files': d.get('changed_files', 0),
            'comments': d.get('comments', 0) + d.get('review_comments', 0),
            'hours_to_merge': hours_to_merge,
            'user': d.get('user', {}).get('login', 'unknown'),
            'merged': d.get('merged_at') is not None
        })
    
    return detailed

def fetch_onboarding_files(repo, token):
    """Checks for existence of key onboarding files in the repository."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    
    files_to_check = [
        'README.md', 'CONTRIBUTING.md', 'CODE_OF_CONDUCT.md',
        '.env.example', 'Makefile', 'docker-compose.yml', 'docker-compose.yaml',
        'Dockerfile', 'setup.sh', 'setup.py', 'pyproject.toml',
        'package.json', '.gitignore', 'LICENSE', '.editorconfig'
    ]
    
    found = {}
    for fname in files_to_check:
        url = f"https://api.github.com/repos/{repo}/contents/{fname}"
        r = requests.get(url, headers=headers)
        found[fname] = r.status_code == 200
    
    # Check if README has a "Getting Started" section
    readme_url = f"https://api.github.com/repos/{repo}/contents/README.md"
    r = requests.get(readme_url, headers=headers)
    has_getting_started = False
    if r.status_code == 200:
        content = base64.b64decode(r.json().get('content', '')).decode('utf-8', errors='ignore').lower()
        has_getting_started = any(s in content for s in ['getting started', 'quick start', 'installation', 'how to use', 'setup'])
    
    found['has_getting_started_section'] = has_getting_started
    return found

def fetch_commit_patterns(repo, token):
    """Fetches last 100 commits for pattern analysis."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    
    url = f"https://api.github.com/repos/{repo}/commits?per_page=100"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return []
    
    commits = r.json()
    results = []
    
    for c in commits:
        commit_data = c.get('commit', {})
        author = commit_data.get('author', {})
        
        date_str = author.get('date', '')
        if date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            continue
        
        results.append({
            'sha': c.get('sha', '')[:7],
            'message': commit_data.get('message', '').split('\n')[0][:80],
            'author': author.get('name', 'Unknown'),
            'day': dt.strftime('%A'),
            'hour': dt.hour,
            'date': dt.strftime('%Y-%m-%d')
        })
    
    return results

def fetch_env_files(repo, token):
    """Fetches environment configuration files for drift analysis."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    
    files_to_fetch = {
        'Dockerfile': None,
        'docker-compose.yml': None,
        'docker-compose.yaml': None,
        '.dockerignore': None,
        '.env.example': None,
        '.node-version': None,
        '.python-version': None,
        '.nvmrc': None,
        '.tool-versions': None,
    }
    
    for fname in list(files_to_fetch.keys()):
        url = f"https://api.github.com/repos/{repo}/contents/{fname}"
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            if 'content' in data:
                files_to_fetch[fname] = base64.b64decode(data['content']).decode('utf-8', errors='ignore')
    
    # Also grab workflow YAML for version cross-referencing
    wf_url = f"https://api.github.com/repos/{repo}/contents/.github/workflows"
    r = requests.get(wf_url, headers=headers)
    workflow_content = ""
    if r.status_code == 200:
        for f in r.json():
            if f['name'].endswith(('.yml', '.yaml')):
                fr = requests.get(f['download_url'], headers=headers)
                if fr.status_code == 200:
                    workflow_content += fr.text + "\n"
    files_to_fetch['_workflows'] = workflow_content if workflow_content else None
    
    return files_to_fetch
