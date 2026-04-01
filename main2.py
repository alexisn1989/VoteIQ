from fastapi import FastAPI
import os
import json

app = FastAPI()




@app.get("/districts")
def get_districts():
    folder = os.path.dirname(os.path.abspath(__file__))
    # This line grabs a list of EVERY file in your folder
    files_in_folder = os.listdir(folder)

    file_name = "va_senate_districts.json"
    file_path = os.path.join(folder, file_name)

    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    else:
        # This will tell us exactly what Python is seeing
        return {
            "error": "File not found",
            "what_python_sees": files_in_folder,
            "looking_for": file_name
        }