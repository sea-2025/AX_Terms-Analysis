import contextlib
import io
import json
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
import analyze


def response():
    return {"categories": [{"category": category, "status": "not_found", "summary": "", "evidence": []}
                           for category in analyze.CATEGORIES]}


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.items = analyze.paragraphs("제목\n내용입니다.\n\n다른 문단입니다.")

    def test_env_file_reads_only_supported_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("# comment\nOPENAI_API_KEY='key-value'\nOPENAI_MODEL=model-name\nOTHER=ignored\n", encoding="utf-8")
            self.assertEqual(analyze.read_env_file(path), {
                "OPENAI_API_KEY": "key-value", "OPENAI_MODEL": "model-name"})

    def test_exact_quote_and_paragraph(self):
        data = response()
        data["categories"][0].update(status="found", summary="설명", evidence=[{"paragraph_id": "P0001", "quote": "제목\n내용입니다."}])
        result, failed = analyze.validate(data, self.items)
        self.assertFalse(failed)
        self.assertEqual(result[0]["근거"][0]["문단 번호"], "P0001")

    def test_paragraph_id_copies_original_evidence(self):
        data = response()
        data["categories"][0].update(status="found", summary="설명", evidence=[{"paragraph_id": "P0001"}])
        result, failed = analyze.validate(data, self.items)
        self.assertFalse(failed)
        self.assertEqual(result[0]["근거"], [{"문단 번호": "P0001", "근거 원문": "제목\n내용입니다."}])

    def test_unknown_paragraph_id_fails(self):
        data = response()
        data["categories"][0].update(status="found", summary="설명", evidence=[{"paragraph_id": "P9999"}])
        result, failed = analyze.validate(data, self.items)
        self.assertTrue(failed)
        self.assertEqual(result[0]["확인 상태"], "근거 확인 실패")
        self.assertEqual(result[0]["근거"], [])

    def test_sample_collection_and_rights_keep_original_paragraphs(self):
        sample = (Path(__file__).resolve().parent.parent / "sample.txt").read_text(encoding="utf-8-sig")
        items = analyze.paragraphs(sample)
        collection = next(p for p in items if "수집 · 이용 목적" in p["text"])
        rights = next(p for p in items if "거부 권리 및 불이익" in p["text"])
        data = response()
        data["categories"][0].update(status="found", summary="수집 목적",
                                     evidence=[{"paragraph_id": collection["id"]}])
        data["categories"][5].update(status="found", summary="동의 거부 권리",
                                     evidence=[{"paragraph_id": rights["id"]}])
        result, failed = analyze.validate(data, items)
        self.assertFalse(failed)
        self.assertEqual(result[0]["근거"][0]["근거 원문"], collection["text"])
        self.assertEqual(result[5]["근거"][0]["근거 원문"], rights["text"])

    def test_valid_rights_summary_is_preserved(self):
        data = response()
        summary = "조건에 따라 법령에 따른 보상을 받을 수 있습니다."
        data["categories"][5].update(status="found", summary=summary,
                                     evidence=[{"paragraph_id": "P0001"}])
        result, failed = analyze.validate(data, self.items)
        self.assertFalse(failed)
        self.assertEqual(result[5]["쉬운 설명"], summary)

    def test_absent_right_is_not_added_by_postprocessing(self):
        data = response()
        data["categories"][5].update(status="found", summary="동의를 거부할 수 있습니다.",
                                     evidence=[{"paragraph_id": "P0001"}])
        result, failed = analyze.validate(data, self.items)
        self.assertFalse(failed)
        self.assertEqual(result[5]["쉬운 설명"], "동의를 거부할 수 있습니다.")

    def test_api_audit_contains_only_response_metadata(self):
        payload = {"id": "chatcmpl-test", "model": "test-model",
                   "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response())}}]}
        class FakeResponse(io.BytesIO):
            status = 200
            headers = {"x-request-id": "req-test"}
        audit = {}
        with patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(payload).encode("utf-8"))):
            data = analyze.call_openai([{"id": "P0001", "text": "private document"}],
                                       "test-model", "private-key", audit)
        self.assertEqual(data, response())
        self.assertEqual(audit["request_id"], "req-test")
        self.assertEqual(audit["http_status"], 200)
        self.assertNotIn("private document", json.dumps(audit))
        self.assertNotIn("private-key", json.dumps(audit))

    def test_invalid_quote_is_not_reported_as_absent(self):
        data = response()
        data["categories"][0].update(status="found", summary="설명", evidence=[{"paragraph_id": "P0002", "quote": "내용입니다."}])
        result, failed = analyze.validate(data, self.items)
        self.assertTrue(failed)
        self.assertEqual(result[0]["확인 상태"], "근거 확인 실패")
        self.assertEqual(result[0]["근거"], [])

    def test_malformed_response_is_error(self):
        data = response()
        data["categories"].pop()
        with self.assertRaises(analyze.ResponseError):
            analyze.validate(data, self.items)

    def test_contradictory_absence_is_error(self):
        data = response()
        data["categories"][0]["summary"] = "설명"
        with self.assertRaises(analyze.ResponseError):
            analyze.validate(data, self.items)

    def test_api_failure_does_not_overwrite_result_or_expose_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.txt", Path(directory) / "result.json"
            source.write_text("공개 문서", encoding="utf-8")
            output.write_text("old", encoding="utf-8")
            err = io.StringIO()
            with patch.dict("os.environ", {"OPENAI_API_KEY": "private-key", "OPENAI_MODEL": "test-model"}), \
                 patch("sys.argv", ["analyze.py", str(source), "--output", str(output)]), \
                 patch("analyze.call_openai", side_effect=urllib.error.URLError("private-key 공개 문서")), \
                 contextlib.redirect_stderr(err):
                self.assertEqual(analyze.main(), 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "old")
            self.assertNotIn("private-key", err.getvalue())
            self.assertNotIn("공개 문서", err.getvalue())

    def test_cli_still_writes_result_after_shared_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.txt"
            output = Path(directory) / "output.json"
            source.write_text("문서 원문", encoding="utf-8")
            data = response()
            data["categories"][0].update(status="found", summary="설명",
                                         evidence=[{"paragraph_id": "P0001"}])
            stdout = types.SimpleNamespace(buffer=io.BytesIO())
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "test-model"}), \
                 patch("sys.argv", ["analyze.py", str(source), "--output", str(output)]), \
                 patch("analyze.call_openai", return_value=data), patch("sys.stdout", stdout):
                self.assertEqual(analyze.main(), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["항목"][0]["근거"][0]["근거 원문"], "문서 원문")
            self.assertEqual(json.loads(stdout.buffer.getvalue()), result)

    def test_invalid_model_json_is_error(self):
        with self.assertRaises(analyze.ResponseError):
            analyze.validate(json.loads('{"categories":[]}'), self.items)


if __name__ == "__main__":
    unittest.main()
