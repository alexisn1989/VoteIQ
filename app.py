# Thin shim so gunicorn can find the FastAPI app.
# render.yaml uses: gunicorn app:app -k uvicorn.workers.UvicornWorker
# The actual app lives in main.py.
from main import app  # noqa: F401
