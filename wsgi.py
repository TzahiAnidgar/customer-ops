"""
Minimal WSGI application for Azure App Service
"""
import os
from flask import Flask

app = Flask(__name__)

@app.route('/')
def index():
    return '<h1>✅ Flask Working!</h1><p>Customer Ops Portal</p>'

@app.route('/health')
def health():
    return {'status': 'ok'}, 200

if __name__ == '__main__':
    app.run()
