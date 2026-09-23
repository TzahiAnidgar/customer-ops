#!/usr/bin/env python3
"""
Push local files to GitHub repository using the GitHub API.
"""
import base64
import os
import requests

# Configuration
OWNER = "TzahiAnidgar"
REPO = "customer-ops"
BASE_PATH = r"C:\Users\ZivMovshowitz\OneDrive - YouCC Technologies\Documents\Microsoft Scout\customer-ops-clean"

# Files to upload (local_path: github_path)
FILES_TO_UPLOAD = {
    "Procfile": "Procfile",
    "runtime.txt": "runtime.txt",
    "requirements.txt": "requirements.txt",
    "wsgi.py": "wsgi.py",
    "README.md": "README.md",
}

# Get token from environment or user input
token = os.environ.get("GITHUB_TOKEN")
if not token:
    token = input("GitHub Personal Access Token (press Ctrl+C to skip): ").strip()
    if not token:
        print("\nToken required. You can also set GITHUB_TOKEN environment variable.")
        print("Alternatively, upload files manually at:")
        print("https://github.com/TzahiAnidgar/customer-ops/upload")
        exit(1)

BASE_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/"
HEADERS = {
    "Authorization": f"token {token}",
    "Accept": "application/vnd.github.v3+json"
}

def read_file_or_template(file_name):
    """Read file from disk or return template."""
    file_path = os.path.join(BASE_PATH, file_name)
    
    # Special case for README.md
    if file_name == "README.md" and not os.path.exists(file_path):
        return """# Customer Operations Flask App

A Flask-based application for managing customers, projects, and tasks with support for project renaming without data loss.

## Deployment

This repository is configured for Railway deployment with:
- `Procfile` - defines how to run the app
- `runtime.txt` - Python version
- `requirements.txt` - Python dependencies
- `wsgi.py` - WSGI entry point

## Local Development

```bash
python -m pip install -r requirements.txt
python wsgi.py
```

Visit http://localhost:5000

## Database

SQLite database stored at `data/customer_ops.db`
"""
    
    if os.path.exists(file_path):
        with open(file_path, 'rb') as f:
            return f.read()
    return None

def upload_file(file_name, github_path):
    """Upload a single file to GitHub."""
    content = read_file_or_template(file_name)
    
    if content is None:
        print(f"[SKIP] {github_path} - file not found")
        return False
    
    # Handle both bytes and strings
    if isinstance(content, str):
        content = content.encode('utf-8')
    
    encoded_content = base64.b64encode(content).decode('utf-8')
    
    url = BASE_URL + github_path
    data = {
        "message": f"Add {github_path}",
        "content": encoded_content
    }
    
    response = requests.put(url, json=data, headers=HEADERS)
    
    if response.status_code in [201, 200]:
        print(f"[OK] {github_path}")
        return True
    elif response.status_code == 422:  # File exists
        print(f"[EXISTS] {github_path} (already in repo)")
        return True
    else:
        print(f"[FAIL] {github_path} - Status {response.status_code}")
        print(f"       Response: {response.text}")
        return False

print(f"Uploading to {OWNER}/{REPO}...\n")

uploaded = 0
for local_file, github_path in FILES_TO_UPLOAD.items():
    if upload_file(local_file, github_path):
        uploaded += 1

print(f"\nUploaded {uploaded}/{len(FILES_TO_UPLOAD)} files")
