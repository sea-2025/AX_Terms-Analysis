"""Run browser smoke tests against the hosted WSGI adapter without API calls."""
import os
import subprocess
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = make_server("127.0.0.1", 0, app.app, handler_class=QuietHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env = os.environ.copy()
    env["TERMS_TEST_URL"] = f"http://127.0.0.1:{server.server_port}/"
    try:
        result = subprocess.run(["node", str(ROOT / "tests" / "test_browser.cjs")], cwd=ROOT, env=env, check=False)
        sys.exit(result.returncode)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
