#!/usr/bin/env python
import os
from pathlib import Path

# Change to the correct directory
os.chdir(r"c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election")

# Load .env
from dotenv import load_dotenv
load_dotenv()

# Verify keys are loaded
print(f"ANTHROPIC_API_KEY loaded: {bool(os.getenv('ANTHROPIC_API_KEY'))}")
print(f"Working directory: {os.getcwd()}")

# Now import and run the main app
if __name__ == "__main__":
    import main
