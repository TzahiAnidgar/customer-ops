"""
Minimal WSGI entry point for Azure App Service
"""
import os
import sys
from pathlib import Path

# Add the app directory to the path
app_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(app_dir))

print("[STARTUP] wsgi.py initializing...")

# Test 1: Create directories
try:
    data_dir = Path(os.getenv("CUSTOMER_OPS_DATA_DIR", str(app_dir.parent / "data")))
    upload_dir = Path(os.getenv("CUSTOMER_OPS_UPLOAD_DIR", str(app_dir.parent / "uploads")))
    
    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    print("[STARTUP] Directories created successfully")
except Exception as e:
    print(f"[STARTUP ERROR] Could not create directories: {e}")

# Test 2: Import Flask
try:
    from flask import Flask
    print("[STARTUP] Flask imported successfully")
except Exception as e:
    print(f"[STARTUP ERROR] Failed to import Flask: {e}")
    sys.exit(1)

# Test 3: Try to import the actual app
print("[STARTUP] Attempting to import app.app...")
try:
    from app.app import create_app
    print("[STARTUP] app.app imported successfully")
    
    app = create_app()
    print("[STARTUP] Flask app created successfully")
    
except Exception as e:
    print(f"[STARTUP ERROR] Failed to create app: {e}")
    import traceback
    
    # Fallback: Return a minimal app
    print("[STARTUP] Creating fallback minimal app...")
    from flask import Flask
    app = Flask(__name__)
    
    @app.route('/')
    def error_home():
        return f'<h1>Error</h1><pre>{str(e)}</pre>', 500

print("[STARTUP] WSGI app is ready!")

if __name__ == "__main__":
    app.run()
