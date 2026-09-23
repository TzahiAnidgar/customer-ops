#!/usr/bin/env python3
import base64
import json
import requests
import sys

def push_file_to_github(owner, repo, file_path, github_path, token):
    """Push a file to GitHub using the API."""
    
    with open(file_path, 'rb') as f:
        content = f.read()
    
    encoded = base64.b64encode(content).decode('utf-8')
    
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{github_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "message": f"Add {github_path}",
        "content": encoded
    }
    
    response = requests.put(url, headers=headers, json=data)
    
    if response.status_code in [201, 200]:
        return True, "OK"
    elif response.status_code == 422:
        return True, "EXISTS"
    else:
        return False, f"HTTP {response.status_code}"

# Configuration
OWNER = "TzahiAnidgar"
REPO = "customer-ops"
BASE_PATH = r"C:\Users\ZivMovshowitz\OneDrive - YouCC Technologies\Documents\Microsoft Scout\customer-ops-clean"

FILES = {
    "Procfile": "Procfile",
    "runtime.txt": "runtime.txt",
    "requirements.txt": "requirements.txt",
    "wsgi.py": "wsgi.py",
}

# Get token from user
token = input("Paste your GitHub personal access token: ").strip()

if not token:
    print("Token is required!")
    sys.exit(1)

print(f"\nDeploying to {OWNER}/{REPO}...\n")

uploaded = 0
for local_file, github_path in FILES.items():
    try:
        file_path = BASE_PATH + "\\" + local_file
        success, msg = push_file_to_github(OWNER, REPO, file_path, github_path, token)
        
        if success:
            status = "✓" if msg == "OK" else "●"
            print(f"{status} {github_path:25} [{msg}]")
            if msg == "OK":
                uploaded += 1
        else:
            print(f"✗ {github_path:25} [{msg}]")
    except FileNotFoundError:
        print(f"✗ {github_path:25} [FILE NOT FOUND]")
    except Exception as e:
        print(f"✗ {github_path:25} [{str(e)[:40]}]")

print(f"\nResult: {uploaded}/{len(FILES)} new files pushed")

if uploaded >= 3:
    print("\n✓ SUCCESS! Files are ready on GitHub.")
    print("\nNext: Go to Railway and connect the repo")
    print("https://railway.com/project/af7dfb0e-758b-4b08-a750-e6dde3563dd5/service/8a96365c-7bd7-4dc3-9513-e7d8e5d4cdcc/settings")
