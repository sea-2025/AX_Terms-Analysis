"""Local-only web interface for the existing document analyzer."""
import json
import os
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import analyze
import product_analysis

ROOT = Path(__file__).resolve().parent
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/products": ("products.html", "text/html; charset=utf-8"),
    "/products.js": ("products.js", "text/javascript; charset=utf-8"),
    "/products.css": ("products.css", "text/css; charset=utf-8"),
}
busy = threading.BoundedSemaphore(1)


class LocalServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # Never log the request, document, analysis result, or credentials.
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def headers_for(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.headers_for(status, "application/json; charset=utf-8", len(data))
        self.wfile.write(data)

    def valid_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.valid_host() or self.path not in ASSETS:
            self.send_error(404)
            return
        filename, mime = ASSETS[self.path]
        content = (ROOT / "web_static" / filename).read_bytes()
        self.headers_for(200, mime, len(content))
        self.wfile.write(content)

    def do_POST(self):
        if (self.path not in ("/api/analyze", "/api/products") or not self.valid_host()
                or self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}"):
            self.send_json(403, {"error": "요청을 확인할 수 없습니다. 이 페이지에서 다시 시도해 주세요."})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self.send_json(400, {"error": "요청 형식을 확인해 주세요."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            if self.path == "/api/analyze":
                text, scope = data.get("text"), data.get("scope")
                if not isinstance(text, str) or not text.strip() or len(text) > 100000:
                    raise ValueError()
                if scope not in ("full", "excerpt", "unknown"):
                    raise ValueError()
            else:
                product_name, documents = product_analysis.prepare_documents(data.get("product_name"), data.get("documents"))
        except product_analysis.InputError as error:
            self.send_json(400, {"error": str(error)})
            return
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "문서 내용을 입력하고 입력 범위를 선택해 주세요. 본문은 100,000자까지 가능합니다."})
            return
        if not busy.acquire(blocking=False):
            self.send_json(429, {"error": "이미 분석 중입니다. 완료된 후 다시 시도해 주세요."})
            return
        try:
            settings = analyze.read_env_file(ROOT / ".env")
            key = (os.getenv("OPENAI_API_KEY") or settings.get("OPENAI_API_KEY", "")).strip()
            model = (os.getenv("OPENAI_MODEL") or settings.get("OPENAI_MODEL", "")).strip()
            if not key or not model:
                self.send_json(503, {"error": "서버에 API 키와 모델이 설정되지 않았습니다. README의 설정 방법을 확인해 주세요."})
                return
            if self.path == "/api/analyze":
                result, _ = analyze.analyze_text(text, model, key)
                result["입력 범위"] = scope
            else:
                payload = product_analysis.build_payload(product_name, documents, model)
                raw = product_analysis.call_model(payload, key)
                result = product_analysis.validate_response(raw, product_name, documents)
                result = product_analysis.add_display_highlights(result, documents)
            self.send_json(200, result)
        except product_analysis.InputError as error:
            self.send_json(400, {"error": str(error)})
        except product_analysis.RuleConfigurationError:
            self.send_json(503, {"error": "분석 규칙 파일을 읽거나 확인할 수 없습니다. 서버 설정을 확인해 주세요."})
        except (OSError, UnicodeError, urllib.error.URLError, analyze.ResponseError, product_analysis.ModelError, ValueError):
            self.send_json(502, {"error": "분석을 완료하지 못했습니다. API 설정과 연결 상태를 확인한 뒤 다시 시도해 주세요."})
        finally:
            busy.release()


if __name__ == "__main__":
    server = LocalServer(("127.0.0.1", 8000), Handler)
    print("http://127.0.0.1:8000", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
