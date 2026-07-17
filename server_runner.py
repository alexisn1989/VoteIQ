#!/usr/bin/env python3
"""Start the FastAPI server with proper environment setup."""
import os
import sys

# Ensure correct working directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env
env_path = ".env"
if os.path.exists(env_path):
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()

print("Environment loaded")
print(f"  ANTHROPIC_API_KEY: {bool(os.getenv('ANTHROPIC_API_KEY'))}")
print(f"  Working directory: {os.getcwd()}")

# Now import and run the app using uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
