"""Run the browser checks against an ephemeral local server without API calls."""
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import web


if __name__ == "__main__":
    server = web.LocalServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    env["TERMS_TEST_URL"] = f"http://127.0.0.1:{server.server_port}/"
    try:
        completed = subprocess.run(
            ["node", str(ROOT / "tests" / "test_browser.cjs")],
            cwd=ROOT, env=env, check=False)
        sys.exit(completed.returncode)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
