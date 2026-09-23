#!/bin/bash
set -e

echo "Build script starting..."

# If customer-ops-clean.zip exists, extract it
if [ -f "customer-ops-clean.zip" ]; then
    echo "Extracting customer-ops-clean.zip..."
    unzip -q customer-ops-clean.zip
    
    # Move all contents to root if they're in a subfolder
    if [ -d "customer-ops-clean" ]; then
        shopt -s dotglob
        mv customer-ops-clean/* .
        rmdir customer-ops-clean
    fi
    
    echo "Extraction complete!"
fi

# Ensure wsgi.py exists at root
if [ ! -f "wsgi.py" ]; then
    echo "ERROR: wsgi.py not found after extraction"
    exit 1
fi

echo "Build completed successfully"