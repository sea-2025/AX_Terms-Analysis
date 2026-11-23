import io
import json
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import product_analysis as product

FIXTURE = Path(__file__).parent / "fixtures" / "fictional-deposit.txt"


def make_doc(text=None, name="가상 상품설명서", product_name="테스트 정기예금"):
    return {"name": name, "product_name": product_name, "scope": "full", "date": "2026년 1월 1일",
            "text": FIXTURE.read_text(encoding="utf-8") if text is None else text}


def raw_result(documents):
    blocks = documents[0]["blocks"]
    def find(term):
        return next(block for block in blocks if term in block["text"])
    rate, priority, fee, early, after = (find(x) for x in ("■ 기본금리", "■ 우대금리", "■ 수수료", "■ 중도해지", "■ 만기 후"))
    def evidence(block):
        return {"document_id": "D001", "block_id": block["id"], "quote": block["text"]}
    def fact(statement, block, conditions=None, status="found"):
        return {"statement": statement, "important_conditions": conditions or [], "conditions": [],
                "status": status, "evidence": [evidence(block)]}
    date_block = find("기준일")
    return {"product": {"suitability": "suitable", "consistency": "consistent", "note": ""},
            "document_dates": [{"document_id": "D001", "date": "2026년 1월 1일", "status": "found",
                                "evidence": [evidence(date_block)]}],
            "categories": [
                {"category": product.CATEGORIES[0], "summary": "기간별 기본금리와 우대조건이 있습니다.",
                 "facts": [fact("6개월 연 2.0%, 12개월 연 2.4%", rate, ["세전"]),
                           fact("우대금리 연 0.3%p", priority, ["월 50만원 이상 3회, 6개월 유지", "중도해지 시 미적용"])],
                 "unknowns": ["고정·변동 여부 미확인"]},
                {"category": product.CATEGORIES[1], "summary": "일부 수수료가 있으며 별도 표 확인이 필요합니다.",
                 "facts": [fact("특정 업무에 수수료가 부과됩니다.", fee),
                           fact("인터넷 조회 수수료가 면제됩니다.", fee)],
                 "unknowns": ["수수료 금액은 별도 수수료표에서 확인"]},
                {"category": product.CATEGORIES[2], "summary": "실제 예치기간별 중도해지이율이 있습니다.",
                 "facts": [fact("3개월 미만은 기본금리의 20%", early, ["최저 연 0.1%"]),
                           fact("만기 후 이율은 연 0.2%로 중도해지이율과 다릅니다.", after)],
                 "unknowns": []}]}


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.name, self.docs = product.prepare_documents("테스트 정기예금", [make_doc()])

    def test_single_request_contains_sections_and_every_document(self):
        docs = [make_doc(), make_doc("표 제목\n기간 | 금리\n6개월 | 2.0%\n\n※ 각주 내용", "가상 금리표")]
        name, prepared = product.prepare_documents("테스트 정기예금", docs)
        payload = json.loads(product.build_payload(name, prepared, "gpt-4.1-mini"))
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        system = payload["messages"][0]["content"]
        for filename, _, _ in product.RULE_FILES:
            content = (product.RULES_DIR / filename).read_text(encoding="utf-8").rstrip()
            self.assertIn("[규칙 파일: " + filename + "]", system)
            self.assertIn(content, system)
        ids = product.RULE_ID_PATTERN.findall(system)
        expected = {f"{prefix}-{i:03d}" for _, prefix, count in product.RULE_FILES for i in range(1, count + 1)}
        self.assertEqual(len(ids), 76)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), expected)
        self.assertNotIn(docs[0]["text"], system)
        data = json.loads(payload["messages"][1]["content"])
        self.assertEqual([d["id"] for d in data["documents"]], ["D001", "D002"])
        self.assertEqual(data["documents"][0]["text"], docs[0]["text"])
        self.assertEqual(data["documents"][1]["text"], docs[1]["text"])
        self.assertIn("※ 각주 내용", data["documents"][1]["blocks"][1]["text"])
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(payload["max_tokens"], product.OUTPUT_TOKENS)

    def test_transport_one_attempt_and_no_retry_on_error(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError("test")
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(urllib.error.URLError):
                product.call_model(product.build_payload(self.name, self.docs, "gpt-4.1-mini"), "test-key")
        opener.open.assert_called_once()

    def test_successful_transport_one_attempt(self):
        body = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(raw_result(self.docs))}}]}
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
        with patch("urllib.request.build_opener", return_value=opener):
            returned = product.call_model(product.build_payload(self.name, self.docs, "gpt-4.1-mini"), "test-key")
        self.assertEqual(returned["product"]["suitability"], "suitable")
        opener.open.assert_called_once()

    def test_input_limit_blocks_before_call(self):
        with self.assertRaises(product.InputError):
            product.prepare_documents("테스트 정기예금", [make_doc("가" * 10001)])

    def test_character_limit_is_total_and_preserves_boundary_text(self):
        name, docs = product.prepare_documents("테스트 정기예금", [make_doc("가" * 5000), make_doc("나" * 5000)])
        payload = json.loads(product.build_payload(name, docs, "gpt-4.1-mini"))
        submitted = json.loads(payload["messages"][1]["content"])["documents"]
        self.assertEqual([len(document["text"]) for document in submitted], [5000, 5000])
        with self.assertRaises(product.InputError):
            product.prepare_documents("테스트 정기예금", [make_doc("가" * 5000), make_doc("나" * 5001)])

    def test_unknown_model_and_context_or_output_limits_fail_closed(self):
        with self.assertRaises(product.InputError):
            product.build_payload(self.name, self.docs, "unknown-model")
        with patch.dict(product.MODEL_LIMITS, {"small-context": (100, 16384), "small-output": (100000, 100)}):
            for model in ("small-context", "small-output"):
                with self.assertRaises(product.InputError):
                    product.build_payload(self.name, self.docs, model)

    def test_missing_duplicate_or_missing_rule_id_prevents_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name, _, _ in product.RULE_FILES:
                shutil.copyfile(product.RULES_DIR / name, target / name)
            with patch.object(product, "RULES_DIR", target):
                rules = target / "fees.md"
                original = rules.read_text(encoding="utf-8")
                line = next(line for line in original.splitlines() if "FEE-001 |" in line)
                rules.write_text(original + "\n" + line + "\n", encoding="utf-8")
                with self.assertRaises(product.RuleConfigurationError):
                    product.build_payload(self.name, self.docs, "gpt-4.1-mini")
                rules.write_text(original.replace(line + "\n", "", 1), encoding="utf-8")
                with self.assertRaises(product.RuleConfigurationError):
                    product.build_payload(self.name, self.docs, "gpt-4.1-mini")
                rules.unlink()
                with self.assertRaises(product.RuleConfigurationError):
                    product.build_payload(self.name, self.docs, "gpt-4.1-mini")

    def test_different_product_names_blocked(self):
        with self.assertRaises(product.InputError):
            product.prepare_documents("테스트 정기예금", [make_doc(product_name="다른 상품")])

    def test_conditions_and_rates_preserved(self):
        result = product.validate_response(raw_result(self.docs), self.name, self.docs)
        self.assertEqual(result["processing_status"], "complete")
        rates, fees, early = result["categories"]
        self.assertIn("6개월 연 2.0%", rates["facts"][0]["statement"])
        self.assertIn("3회", rates["facts"][1]["important_conditions"][0])
        self.assertIn("중도해지 시 미적용", rates["facts"][1]["important_conditions"])
        self.assertTrue(any("별도 수수료표" in item for item in fees["unknowns"]))
        self.assertTrue(any("만기 후" in f["statement"] for f in early["facts"]))
        self.assertIn("3개월 미만", early["facts"][0]["statement"])

    def test_four_fee_cases_remain_distinct(self):
        data = raw_result(self.docs)
        fee = data["categories"][1]
        grounded = fee["facts"][0]["evidence"]
        fee["facts"] = [
            {"statement": "특정 수수료 없음", "important_conditions": [], "conditions": [], "status": "found", "evidence": grounded},
            {"statement": "미기재", "important_conditions": [], "conditions": [], "status": "not_found_in_input", "evidence": []},
            {"statement": "금액 미확인", "important_conditions": [], "conditions": [], "status": "needs_review", "evidence": grounded},
            {"statement": "별도 표 참조", "important_conditions": [], "conditions": [], "status": "needs_review", "evidence": grounded}]
        result = product.validate_response(data, self.name, self.docs)
        self.assertEqual([f["status"] for f in result["categories"][1]["facts"]],
                         ["found", "not_found_in_input", "needs_review", "needs_review"])
        self.assertEqual(result["categories"][1]["facts"][1]["statement"], "입력에서 관련 내용을 확인하지 못했습니다.")

    def test_bad_block_or_quote_hides_fact_and_summary_only_for_affected_category(self):
        for field, value in (("block_id", "P9999"), ("quote", "없는 문장"), ("document_id", "D999")):
            data = raw_result(self.docs)
            data["categories"][0]["facts"][0]["evidence"][0][field] = value
            result = product.validate_response(data, self.name, self.docs)
            self.assertEqual(result["processing_status"], "partial")
            self.assertEqual(result["categories"][0]["facts"][0]["status"], "validation_failed")
            self.assertNotIn("2.0%", result["categories"][0]["summary"])
            self.assertEqual(result["categories"][1]["facts"][0]["status"], "found")

    def test_newline_normalization_tracks_original_positions(self):
        text = "첫 제목\r\n첫 내용\r\n\r\n둘째 제목\r\n둘째 내용"
        name, docs = product.prepare_documents("테스트 정기예금", [make_doc(text)])
        data = raw_result(self.docs)
        data["document_dates"] = [{"document_id": "D001", "date": "", "status": "not_found_in_input", "evidence": []}]
        for category in data["categories"]:
            category["facts"] = []
        data["categories"][0]["facts"] = [{"statement": "설명", "important_conditions": [], "conditions": [], "status": "found",
            "evidence": [{"document_id": "D001", "block_id": "P0001", "quote": "첫 제목\n첫 내용"}]}]
        result = product.validate_response(data, name, docs)
        ev = result["categories"][0]["facts"][0]["evidence"][0]
        self.assertEqual(text[ev["start"]:ev["end"]], ev["quote"])
        self.assertEqual(ev["quote"], "첫 제목\r\n첫 내용")

    def test_document_date_requires_exact_document_evidence(self):
        data = raw_result(self.docs)
        good = product.validate_response(data, self.name, self.docs)
        self.assertEqual(good["documents"][0]["date"], "2026년 1월 1일")
        self.assertEqual(good["documents"][0]["date_evidence"][0]["document_id"], "D001")
        data["document_dates"][0]["date"] = "2027년 1월 1일"
        bad = product.validate_response(data, self.name, self.docs)
        self.assertEqual(bad["processing_status"], "partial")
        self.assertEqual(bad["documents"][0]["date"], "미확인")
        self.assertEqual(bad["documents"][0]["date_status"], "validation_failed")
        self.assertEqual(bad["documents"][0]["date_evidence"], [])
        self.assertNotIn("기간별 기본금리", bad["categories"][0]["summary"])

    def test_truncated_refusal_and_malformed_responses_are_errors(self):
        for choice in ({"finish_reason": "length", "message": {"content": "{}"}},
                       {"finish_reason": "stop", "message": {"refusal": "no", "content": "{}"}},
                       {"finish_reason": "stop", "message": {"content": "{"}}):
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value = io.BytesIO(json.dumps({"choices": [choice]}).encode())
            with patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(product.ModelError):
                    product.call_model(b"{}", "test")
        with self.assertRaises(product.ModelError):
            product.validate_response({"categories": []}, self.name, self.docs)

    def test_conflicting_products_and_non_product_document_are_blocked(self):
        for field, value in (("consistency", "conflicting"), ("suitability", "not_suitable")):
            data = raw_result(self.docs)
            data["product"][field] = value
            result = product.validate_response(data, self.name, self.docs)
            self.assertEqual(result["processing_status"], "blocked")
            self.assertTrue(all(not category["facts"] for category in result["categories"]))

    def test_uncertain_identity_in_multiple_documents_is_blocked(self):
        name, docs = product.prepare_documents("테스트 정기예금", [make_doc(), make_doc("별도 금리표", "가상 금리표")])
        data = raw_result(docs)
        data["product"]["consistency"] = "uncertain"
        data["document_dates"].append({"document_id": "D002", "date": "", "status": "not_found_in_input", "evidence": []})
        result = product.validate_response(data, name, docs)
        self.assertEqual(result["processing_status"], "blocked")
        self.assertTrue(all(not category["facts"] for category in result["categories"]))


if __name__ == "__main__":
    unittest.main()
