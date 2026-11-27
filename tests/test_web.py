import http.client
import io
import json
import threading
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import analyze
import web


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web.LocalServer(("127.0.0.1", 0), web.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, data, origin=True, path="/api/analyze"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = f"http://127.0.0.1:{self.port}"
        connection.request("POST", path, json.dumps(data).encode(), headers)
        response = connection.getresponse()
        status = response.status
        body = json.loads(response.read())
        connection.close()
        return status, body

    def test_empty_input_and_cross_origin_are_rejected(self):
        with patch("analyze.analyze_text") as analysis:
            self.assertEqual(self.request({"text": " ", "scope": "full"})[0], 400)
            self.assertEqual(self.request({"text": "문서", "scope": "full"}, origin=False)[0], 403)
            analysis.assert_not_called()

    def test_success_returns_verified_result_and_scope_without_key(self):
        raw = {"categories": [{"category": category, "status": "not_found", "summary": "", "evidence": []}
                              for category in analyze.CATEGORIES]}
        raw["categories"][0].update(status="found", summary="<script>bad()</script>",
                                    evidence=[{"paragraph_id": "P0001"}])
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "test-model"}), \
             patch("analyze.call_openai", return_value=raw):
            status, result = self.request({"text": "<img src=x>", "scope": "excerpt"})
        self.assertEqual(status, 200)
        self.assertEqual(result["입력 범위"], "excerpt")
        self.assertEqual(len(result["항목"]), 6)
        self.assertEqual(result["항목"][0]["근거"][0]["근거 원문"], "<img src=x>")
        self.assertNotIn("secret", json.dumps(result))

    def test_api_failure_is_generic_and_does_not_echo_text(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "test-model"}), \
             patch("analyze.call_openai", side_effect=urllib.error.URLError("secret document")):
            status, result = self.request({"text": "document", "scope": "unknown"})
        self.assertEqual(status, 502)
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("document", json.dumps(result))

    def test_server_rejects_duplicate_while_busy(self):
        web.busy.acquire()
        try:
            with patch("analyze.analyze_text") as analysis:
                self.assertEqual(self.request({"text": "문서", "scope": "full"})[0], 429)
                analysis.assert_not_called()
        finally:
            web.busy.release()

    def test_invalid_evidence_is_distinct_from_missing(self):
        raw = {"categories": [{"category": category, "status": "not_found", "summary": "", "evidence": []}
                              for category in analyze.CATEGORIES]}
        raw["categories"][0].update(status="found", summary="설명", evidence=[{"paragraph_id": "P9999"}])
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "test-model"}), \
             patch("analyze.call_openai", return_value=raw):
            status, result = self.request({"text": "문서", "scope": "full"})
        self.assertEqual(status, 200)
        self.assertEqual(result["항목"][0]["확인 상태"], "근거 확인 실패")
        self.assertEqual(result["항목"][1]["확인 상태"], "입력에서 확인되지 않음")

    def test_product_request_calls_model_once_for_multiple_documents(self):
        import product_analysis
        raw = {"product": {"suitability": "suitable", "consistency": "consistent", "note": ""},
               "document_dates": [{"document_id": "D001", "date": "", "status": "not_found_in_input", "evidence": []},
                                  {"document_id": "D002", "date": "", "status": "not_found_in_input", "evidence": []}],
               "categories": [{"category": name, "summary": "", "facts": [], "unknowns": []}
                              for name in product_analysis.CATEGORIES]}
        doc = {"name": "가상 약관", "product_name": "가상 예금", "scope": "full", "date": "", "text": "가상 문서"}
        data = {"product_name": "가상 예금", "documents": [doc, dict(doc, name="가상 금리표")]}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "gpt-4.1-mini"}), \
             patch("product_analysis.call_model", return_value=raw) as call:
            status, result = self.request(data, path="/api/products")
        self.assertEqual(status, 200)
        call.assert_called_once()
        self.assertEqual(len(json.loads(json.loads(call.call_args.args[0])["messages"][1]["content"])["documents"]), 2)
        self.assertEqual(result["processing_status"], "complete")
        self.assertNotIn("secret", json.dumps(result))

    def test_product_id_only_response_copies_each_source_paragraph_and_flags_missing_fields(self):
        import product_analysis
        source = "우대 대상은 급여이체 고객입니다.\n\n기간과 예외는 별도 조건을 확인합니다."
        doc = {"name": "가상 약관", "product_name": "가상 예금", "scope": "full", "date": "", "text": source}
        raw = {"product": {"suitability": "suitable", "consistency": "consistent", "note": ""},
               "document_dates": [{"document_id": "D001", "date": "", "status": "not_found_in_input", "evidence": []}],
               "categories": [{"category": c, "summary": "", "facts": [], "unknowns": []} for c in product_analysis.CATEGORIES]}
        raw["categories"][0]["facts"] = [{"statement": "우대금리 조건", "important_conditions": [], "conditions": [],
            "condition_fields": {"applicability": "급여이체", "period_definition": "", "exceptions": ""},
            "status": "found", "evidence": [{"document_id": "D001", "block_id": "P0001"},
                                              {"document_id": "D001", "block_id": "P0002"}]}]
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "gpt-4.1-mini"}), \
             patch("product_analysis.call_model", return_value=raw) as call:
            status, result = self.request({"product_name": "가상 예금", "documents": [doc]}, path="/api/products")
        self.assertEqual(status, 200)
        call.assert_called_once()
        fact = result["categories"][0]["facts"][0]
        self.assertEqual(fact["status"], "needs_review")
        self.assertEqual(fact["condition_fields_missing"], ["period_definition", "exceptions"])
        self.assertEqual([e["quote"] for e in fact["evidence"]], source.split("\n\n"))
        self.assertNotIn("secret", json.dumps(result))

    def test_product_over_limit_and_mixed_product_do_not_call_model(self):
        doc = {"name": "가상 약관", "product_name": "가상 예금", "scope": "full", "date": "", "text": "가" * 10001}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "gpt-4.1-mini"}), \
             patch("product_analysis.call_model") as call:
            status, _ = self.request({"product_name": "가상 예금", "documents": [doc]}, path="/api/products")
            self.assertEqual(status, 400)
            status, _ = self.request({"product_name": "다른 상품", "documents": [doc]}, path="/api/products")
            self.assertEqual(status, 400)
            call.assert_not_called()

    def test_product_model_error_is_failure_not_missing(self):
        import product_analysis
        doc = {"name": "가상 약관", "product_name": "가상 예금", "scope": "full", "date": "", "text": "가상 문서"}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "gpt-4.1-mini"}), \
             patch("product_analysis.call_model", side_effect=product_analysis.ModelError()):
            status, body = self.request({"product_name": "가상 예금", "documents": [doc]}, path="/api/products")
        self.assertEqual(status, 502)
        self.assertIn("error", body)
        self.assertNotIn("categories", body)

    def test_product_malformed_message_returns_safe_json_502(self):
        key = "mock-private-key"
        document = "mock-private-document"
        internal = "mock-internal-detail"
        doc = {"name": "가상 약관", "product_name": "가상 예금", "scope": "full", "date": "", "text": document}
        cases = (("null", {"message": None}), ("array", {"message": []}),
                 ("string", {"message": internal}), ("missing", {}))
        with patch.dict("os.environ", {"OPENAI_API_KEY": key, "OPENAI_MODEL": "gpt-4.1-mini"}), \
             patch("analyze.read_env_file", return_value={}):
            for label, message_field in cases:
                with self.subTest(message=label):
                    choice = {"finish_reason": "stop", **message_field}
                    response = {"choices": [choice]}
                    opener = MagicMock()
                    opener.open.return_value.__enter__.return_value = io.BytesIO(json.dumps(response).encode("utf-8"))
                    with patch("urllib.request.build_opener", return_value=opener):
                        status, body = self.request({"product_name": "가상 예금", "documents": [doc]}, path="/api/products")
                    self.assertEqual(status, 502)
                    self.assertEqual(body, {"error": "분석을 완료하지 못했습니다. API 설정과 연결 상태를 확인한 뒤 다시 시도해 주세요."})
                    self.assertNotIn(key, json.dumps(body))
                    self.assertNotIn(document, json.dumps(body))
                    self.assertNotIn(internal, json.dumps(body))
                    opener.open.assert_called_once()

    def test_product_missing_rules_is_error_and_does_not_call_model(self):
        import product_analysis
        doc = {"name": "가상 약관", "product_name": "가상 예금", "scope": "full", "date": "", "text": "가상 문서"}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "gpt-4.1-mini"}), \
             patch.object(product_analysis, "RULES_DIR", product_analysis.RULES_DIR / "not-existing"), \
             patch("product_analysis.call_model") as call:
            status, body = self.request({"product_name": "가상 예금", "documents": [doc]}, path="/api/products")
        self.assertEqual(status, 503)
        self.assertIn("error", body)
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
