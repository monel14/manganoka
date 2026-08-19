import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Permet aux imports locaux (main.py, routes/, services/, etc.) de fonctionner
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from a2wsgi import ASGIMiddleware
    from main import app

    # FastAPI (ASGI) -> Passenger/WSGI
    application = ASGIMiddleware(app)

except Exception:
    error_log_path = os.path.join(BASE_DIR, "error_startup.log")

    with open(error_log_path, "w", encoding="utf-8") as f:
        f.write("=== Erreur de démarrage de l'application FastAPI ===\n\n")
        f.write(traceback.format_exc())

    raise