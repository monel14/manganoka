import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))

try:
    from a2wsgi import ASGIMiddleware
    from main import app
    application = ASGIMiddleware(app)
except Exception as e:
    # En cas d'erreur de démarrage, on capture le traceback dans un fichier local pour débugger facilement
    error_log_path = os.path.join(os.path.dirname(__file__), "error_startup.log")
    with open(error_log_path, "w") as f:
        f.write("=== Erreur de démarrage de l'application FastAPI ===\n")
        traceback.print_exc(file=f)
    raise e