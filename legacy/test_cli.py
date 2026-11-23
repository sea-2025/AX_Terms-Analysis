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

    def test_exact_quote_and_paragraph(self):
        data = response()
        data["categories"][0].update(status="found", summary="설명", evidence=[{"paragraph_id": "P0001", "quote": "제목\n내용입니다."}])
        result, failed = analyze.validate(data, self.items)
        self.assertFalse(failed)
        self.assertEqual(result[0]["근거"][0]["문단 번호"], "P0001")

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

    def test_invalid_model_json_is_error(self):
        with self.assertRaises(analyze.ResponseError):
            analyze.validate(json.loads('{"categories":[]}'), self.items)

    def test_success_writes_verified_result(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.txt", Path(directory) / "result.json"
            source.write_text("동의 철회 시까지 보유합니다.", encoding="utf-8")
            data = response()
            data["categories"][1].update(
                status="found", summary="동의 철회 시까지 보유합니다.",
                evidence=[{"paragraph_id": "P0001", "quote": "동의 철회 시까지"}])
            stdout = types.SimpleNamespace(buffer=io.BytesIO())
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "test-model"}), \
                 patch("sys.argv", ["analyze.py", str(source), "--output", str(output)]), \
                 patch("analyze.call_openai", return_value=data), patch("sys.stdout", stdout):
                self.assertEqual(analyze.main(), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["분석 상태"], "완료")
            self.assertEqual(result["항목"][1]["근거"][0]["근거 원문"], "동의 철회 시까지")
            self.assertEqual(json.loads(stdout.buffer.getvalue()), result)


if __name__ == "__main__":
    unittest.main()
