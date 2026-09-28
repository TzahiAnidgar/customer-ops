"""
Simple Flask App for Azure App Service
"""
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return '''<!DOCTYPE html>
<html>
<head><title>Customer Ops</title></head>
<body>
<h1>Customer Operations Portal</h1>
<p>App is running on Azure!</p>
<hr>
<p><a href="/status">/status</a></p>
</body>
</html>'''

@app.route('/status')
def status():
    return {'status': 'ok'}, 200

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)