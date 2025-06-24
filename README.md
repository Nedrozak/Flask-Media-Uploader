# Flask Chunked File Uploader
A robust Flask-based file uploader with chunked uploading capability, designed for handling large image and video files securely and efficiently.

## Features
**Chunked Uploads:** 
Supports large file uploads by splitting files into manageable chunks.
**Session Management:** 
Uses Redis to securely handle upload sessions and token management.
**Security:** 
Implements CSRF protection, validates MIME types, and sanitizes filenames.
**Logging:** 
Configurable logging to both console and file with daily rotation.
**Configuration:** 
Environment-driven configuration for easy deployment.

---

## Installation
1. Clone the repository:
    `git clone https://github.com/yourusername/Flask-Media-Uploader.git`

   `cd Flask-Media-Uploader`

3. Create and activate a Python virtual environment:
    `python -m venv venv`

   `source venv/bin/activate`

5. Install dependencies:
    `pip install -r requirements.txt`

---

## Configuration
Set the following environment variables, preferably in a .env file

`FLASK_SECRET_KEY=your_secret_key`

`REDIS_URL=redis://REDIS_URL/0`

`UPLOAD_FOLDER=/path/to/uploads`

`MAX_CONTENT_LENGTH=1073741824`  Maximum file size (1 GB in bytes)

`CHUNK_SIZE=5242880`             5 MB in bytes

---

## Running the Server
Start the server with Waitress: `python serve.py`

Access the application at: `http://localhost:5000`  or  `http://EXTERNAL_IP:5000`

---

## Project Structure

| Path/File                                 | Description                        |
|-------------------------------------------|------------------------------------|
| `app.py`                                  | Flask application logic            |
| `config.py`                               | Application configuration          |
| `logging_config.py`                       | Logging setup                      |
| `serve.py`                                | WSGI server (Waitress) entrypoint  |
| **static/**                               | Static files directory             |
| `static/css/bootstrap.min.css`            | Bootstrap CSS styles               |
| `static/css/styles.css`                   | Custom CSS styles                  |
| `static/favicon/android-chrome-192x192.png` | Android favicon (192px)          |
| `static/favicon/android-chrome-512x512.png` | Android favicon (512px)          |
| `static/favicon/apple-touch-icon.png`     | Apple touch icon                   |
| `static/favicon/favicon-16x16.png`        | Favicon (16px)                     |
| `static/favicon/favicon-32x32.png`        | Favicon (32px)                     |
| `static/favicon/favicon.ico`              | Favicon (.ico)                     |
| `static/favicon/site.webmanifest`         | Web app manifest                   |
| `static/scripts/bootstrap.min.js`         | Bootstrap JS                       |
| `static/scripts/popper.min.js`            | Popper JS (for Bootstrap)          |
| `static/scripts/index.js`                 | File upload client logic           |
| `static/scripts/summary.js`               | Upload summary page logic          |
| **templates/**                            | Jinja templates                    |
| `templates/index.jinja`                   | File upload form template          |
| `templates/summary.jinja`                 | File upload summary display        |
| **uploads/**                              | Directory for uploaded files       |
| **logs/**                                 | Application logs                   |


## License
Distributed under the MIT License. See LICENSE file for details.
