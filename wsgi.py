"""
WSGI entry point for Azure App Service
Loads the actual Flask application from app.app
"""
import sys
import os

# Add app directory to path
sys.path.insert(0, os.path.dirname(__file__))

try:
    from app.app import create_app
    app = create_app()
except Exception as e:
    # Fallback if app.py fails to load
    from flask import Flask
    app = Flask(__name__)
    
    @app.route('/')
    def error():
        return f'''<!DOCTYPE html>
<html>
<head><title>Error</title></head>
<body>
<h1>❌ Error Loading App</h1>
<p>{str(e)}</p>
<hr>
<p><a href="/health">/health</a></p>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
