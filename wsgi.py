import os
import sys
from pathlib import Path

# Add the app directory to the path
app_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(app_dir))

# Import the Flask app
from app.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run()
