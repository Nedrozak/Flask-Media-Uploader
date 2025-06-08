from pathlib import Path

from waitress import serve

import app
from logging_config import setup_logging

Path(app.app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

setup_logging()

serve(app.application, host="0.0.0.0", port=5000, threads=6, url_scheme="http")
