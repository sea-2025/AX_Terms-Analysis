"""Exercise the hosted WSGI adapter without any OpenAI requests."""
import io
import json
import unittest
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

import app as hosted
import product_analysis
from test_product import make_doc, raw_result


class HostedAppTests(unittest.TestCase):
    def request(self, path, method="GET", body=None, origin="https://example.vercel.app", content_type="application/json"):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
        env = {}
        setup_testing_defaults(env)
        env.update({"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "HTTP_HOST": "example.vercel.app",
                    "HTTP_ORIGIN": origin, "CONTENT_TYPE": content_type, "CONTENT_LENGTH": str(len(payload)),
                    "wsgi.input": io.BytesIO(payload), "wsgi.url_scheme": "https"})
        observed = {}
        def start(status, headers, exc_info=None):
            observed.update(status=int(status.split()[0]), headers=dict(headers))
        wrapped = validator(hosted.app)(env, start)
        try:
            observed["body"] = b"".join(wrapped)
        finally:
            wrapped.close()
        return observed

    def test_pages_and_static_assets_are_served(self):
        for path in ("/", "/products", "/products.js", "/products.css", "/app.js", "/style.css"):
            with self.subTest(path=path):
                result = self.request(path)
                self.assertEqual(result["status"], 200)
                self.assertTrue(result["body"])
                self.assertEqual(result["headers"]["Cache-Control"], "no-store")
        self.assertEqual(self.request("/../.env")["status"], 404)

    def test_product_endpoint_calls_model_once_and_reuses_validation(self):
        name = "테스트 정기예금"
        doc = make_doc()
        _, docs = product_analysis.prepare_documents(name, [doc])
        body = {"product_name": name, "documents": [doc]}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "gpt-4.1-mini"}), \
             patch.object(product_analysis, "call_model", return_value=raw_result(docs)) as model, \
             patch.object(product_analysis.urllib.request, "build_opener", side_effect=AssertionError("No API calls")):
            result = self.request("/api/products", "POST", body)
        self.assertEqual(result["status"], 200)
        model.assert_called_once()
        data = json.loads(result["body"])
        self.assertEqual(len(data["categories"]), 3)
        self.assertNotIn(b"test-key", result["body"])

    def test_rejects_cross_origin_and_oversize_without_model_call(self):
        name = "테스트 정기예금"
        body = {"product_name": name, "documents": [make_doc()]}
        with patch.object(product_analysis, "call_model") as model:
            self.assertEqual(self.request("/api/products", "POST", body, origin="https://attacker.example")["status"], 403)
            body["documents"][0]["text"] = "가" * 10001
            self.assertEqual(self.request("/api/products", "POST", body)["status"], 400)
            model.assert_not_called()

    def test_model_error_is_generic_and_does_not_leak_key_or_document(self):
        name = "테스트 정기예금"
        body = {"product_name": name, "documents": [make_doc()]}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "gpt-4.1-mini"}), \
             patch.object(product_analysis, "call_model", side_effect=ValueError("test-key private-input")):
            result = self.request("/api/products", "POST", body)
        self.assertEqual(result["status"], 502)
        self.assertNotIn(b"test-key", result["body"])
        self.assertNotIn(b"private-input", result["body"])


if __name__ == "__main__":
    unittest.main()
