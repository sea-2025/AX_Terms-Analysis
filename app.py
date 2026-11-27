"""WSGI entry point for hosted deployments; the local web.py remains unchanged."""
import json
import os
import threading
import urllib.error
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

import analyze
import product_analysis
from web import ASSETS

ROOT = Path(__file__).resolve().parent
busy = threading.BoundedSemaphore(1)
SECURITY_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
]


def response(start_response, code, content_type, data):
    body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = [("Content-Type", content_type), ("Content-Length", str(len(body))), *SECURITY_HEADERS]
    start_response(f"{code} {HTTPStatus(code).phrase}", headers)
    return [body]


def json_response(start_response, code, data):
    return response(start_response, code, "application/json; charset=utf-8", data)


def same_origin(environ):
    try:
        origin = urlsplit(environ.get("HTTP_ORIGIN", ""))
        host = environ.get("HTTP_HOST", "").lower()
        return (origin.scheme in ("http", "https") and bool(host) and origin.netloc.lower() == host
                and not origin.username and not origin.password and origin.path in ("", "/")
                and not origin.query and not origin.fragment)
    except ValueError:
        return False


def app(environ, start_response):
    method, path = environ.get("REQUEST_METHOD", ""), environ.get("PATH_INFO", "")
    if method == "GET" and path in ASSETS:
        filename, mime = ASSETS[path]
        try:
            content = (ROOT / "web_static" / filename).read_bytes()
        except OSError:
            return json_response(start_response, 503, {"error": "화면을 불러올 수 없습니다."})
        return response(start_response, 200, mime, content)
    if method != "POST" or path not in ("/api/analyze", "/api/products"):
        return json_response(start_response, 404, {"error": "요청한 페이지를 찾을 수 없습니다."})
    if not same_origin(environ):
        return json_response(start_response, 403, {"error": "요청을 확인할 수 없습니다. 이 페이지에서 다시 시도해 주세요."})
    if environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower() != "application/json":
        return json_response(start_response, 400, {"error": "요청 형식을 확인해 주세요."})
    try:
        length = int(environ.get("CONTENT_LENGTH", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError()
        data = json.loads(environ["wsgi.input"].read(length))
        if not isinstance(data, dict):
            raise ValueError()
        if path == "/api/analyze":
            text, scope = data.get("text"), data.get("scope")
            if not isinstance(text, str) or not text.strip() or len(text) > 100000 or scope not in ("full", "excerpt", "unknown"):
                raise ValueError()
        else:
            product_name, documents = product_analysis.prepare_documents(data.get("product_name"), data.get("documents"))
    except product_analysis.InputError as error:
        return json_response(start_response, 400, {"error": str(error)})
    except (KeyError, ValueError, UnicodeError):
        return json_response(start_response, 400, {"error": "문서 내용과 입력 범위를 확인해 주세요."})
    if not busy.acquire(blocking=False):
        return json_response(start_response, 429, {"error": "이미 분석 중입니다. 완료된 후 다시 시도해 주세요."})
    try:
        key, model = os.environ.get("OPENAI_API_KEY", "").strip(), os.environ.get("OPENAI_MODEL", "").strip()
        if not key or not model:
            return json_response(start_response, 503, {"error": "서버에 API 키와 모델이 설정되지 않았습니다."})
        if path == "/api/analyze":
            result, _ = analyze.analyze_text(text, model, key)
            result["입력 범위"] = scope
        else:
            payload = product_analysis.build_payload(product_name, documents, model)
            raw = product_analysis.call_model(payload, key)
            result = product_analysis.validate_response(raw, product_name, documents)
            result = product_analysis.add_display_highlights(result, documents)
        return json_response(start_response, 200, result)
    except product_analysis.InputError as error:
        return json_response(start_response, 400, {"error": str(error)})
    except product_analysis.RuleConfigurationError:
        return json_response(start_response, 503, {"error": "분석 규칙 파일을 확인할 수 없습니다."})
    except Exception:
        # Do not log the upstream error: it may contain credentials or input text.
        return json_response(start_response, 502, {"error": "분석을 완료하지 못했습니다. API 설정과 연결 상태를 확인해 주세요."})
    finally:
        busy.release()
