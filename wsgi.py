"""
Simple Flask WSGI application for Azure App Service
"""
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def index():
    return '''<!DOCTYPE html>
<html>
<head>
    <title>Customer Operations Portal</title>
    <style>body { font-family: Arial; margin: 40px; }</style>
</head>
<body>
    <h1>✅ Flask Running on Azure!</h1>
    <p>Customer Operations Portal</p>
    <hr>
    <p><a href="/health">/health</a></p>
</body>
</html>'''

@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run()
