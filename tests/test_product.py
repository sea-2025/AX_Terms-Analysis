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
        schema = payload["response_format"]["json_schema"]["schema"]
        fact_schema = schema["properties"]["categories"]["items"]["properties"]["facts"]["items"]
        evidence = fact_schema["properties"]["evidence"]["items"]
        self.assertEqual(set(evidence["properties"]), {"document_id", "block_id"})
        self.assertEqual(set(fact_schema["properties"]["condition_fields"]["required"]),
                         {"applicability", "period_definition", "exceptions"})
        self.assertIn("인용문을 작성하지 않는다", system)

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

    def test_rates_and_conditions_remain_available_as_unverified_source(self):
        result = product.validate_response(raw_result(self.docs), self.name, self.docs)
        self.assertEqual(result["processing_status"], "complete")
        rates, fees, early = result["categories"]
        self.assertEqual(rates["facts"][0]["status"], "needs_review")
        self.assertIn("6개월은 연 2.0%", rates["facts"][0]["evidence"][0]["quote"])
        self.assertIn("3회", rates["facts"][1]["evidence"][0]["quote"])
        self.assertIn("중도해지 시 적용하지 않습니다", rates["facts"][1]["evidence"][0]["quote"])
        self.assertIn("별도 수수료표", fees["facts"][0]["evidence"][0]["quote"])
        self.assertIn("만기 후", early["facts"][1]["evidence"][0]["quote"])
        self.assertIn("3개월 미만", early["facts"][0]["evidence"][0]["quote"])
        self.assertTrue(all(not fact["conditions"] for category in result["categories"] for fact in category["facts"]))
        self.assertIn("추가 확인", rates["facts"][1]["important_conditions"][0])

    def test_unverified_fee_claims_are_not_exposed_as_conclusions(self):
        data = raw_result(self.docs)
        fee = data["categories"][1]
        grounded = fee["facts"][0]["evidence"]
        fee["facts"] = [
            {"statement": "특정 수수료 없음", "important_conditions": [], "conditions": [], "status": "found", "evidence": grounded},
            {"statement": "미기재", "important_conditions": [], "conditions": [], "status": "not_found_in_input", "evidence": []},
            {"statement": "금액 미확인", "important_conditions": [], "conditions": [], "status": "needs_review", "evidence": grounded},
            {"statement": "별도 표 참조", "important_conditions": [], "conditions": [], "status": "needs_review", "evidence": grounded}]
        result = product.validate_response(data, self.name, self.docs)
        self.assertEqual([f["status"] for f in result["categories"][1]["facts"]], ["needs_review"] * 4)
        self.assertTrue(all("특정 수수료 없음" not in f["statement"] for f in result["categories"][1]["facts"]))
        self.assertEqual(result["categories"][1]["facts"][1]["evidence"], [])

    def test_unknown_document_or_block_hides_only_the_affected_fact(self):
        for field, value in (("block_id", "P9999"), ("document_id", "D999")):
            data = raw_result(self.docs)
            data["categories"][0]["facts"][0]["evidence"][0][field] = value
            result = product.validate_response(data, self.name, self.docs)
            self.assertEqual(result["processing_status"], "partial")
            self.assertEqual(result["categories"][0]["facts"][0]["status"], "validation_failed")
            self.assertNotIn("2.0%", result["categories"][0]["summary"])
            self.assertEqual(result["categories"][1]["facts"][0]["status"], "needs_review")
            self.assertTrue(result["categories"][1]["facts"][0]["evidence"])

    def test_legacy_model_quote_is_ignored_and_source_block_is_review_only(self):
        data = raw_result(self.docs)
        fact = data["categories"][0]["facts"][1]
        fact["statement"] = "원문에 없는 우대조건을 만족하면 금리가 적용됩니다."
        fact["evidence"][0]["quote"] = "모델이 만들어낸 문장"
        result = product.validate_response(data, self.name, self.docs)
        checked = result["categories"][0]["facts"][1]
        actual = next(b["text"] for b in self.docs[0]["blocks"] if b["id"] == fact["evidence"][0]["block_id"])
        self.assertEqual(checked["status"], "needs_review")
        self.assertEqual(checked["evidence"][0]["quote"], actual)
        self.assertNotIn("모델이 만들어낸 문장", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("원문에 없는 우대조건을 만족하면", json.dumps(result, ensure_ascii=False))
        self.assertEqual(checked["condition_fields_missing"], list(product.CONDITION_FIELDS))

    def test_each_selected_paragraph_is_separate_and_invalid_ids_are_rejected(self):
        data = raw_result(self.docs)
        fact = data["categories"][0]["facts"][1]
        first = fact["evidence"][0]["block_id"]
        second = next(b["id"] for b in self.docs[0]["blocks"] if "■ 중도해지" in b["text"])
        fact["evidence"] = [{"document_id": "D001", "block_id": first},
                            {"document_id": "D001", "block_id": second}]
        result = product.validate_response(data, self.name, self.docs)
        checked = result["categories"][0]["facts"][1]
        self.assertEqual(checked["status"], "needs_review")
        self.assertEqual([ev["block_id"] for ev in checked["evidence"]], [first, second])
        for ev in checked["evidence"]:
            self.assertEqual(self.docs[0]["_source"][ev["start"]:ev["end"]], ev["quote"])
        fact["evidence"][1]["block_id"] = "P9999"
        invalid = product.validate_response(data, self.name, self.docs)
        self.assertEqual(invalid["categories"][0]["facts"][1]["status"], "validation_failed")
        self.assertEqual(invalid["categories"][0]["facts"][1]["evidence"], [])
        self.assertEqual(invalid["categories"][1]["facts"][0]["status"], "needs_review")

    def test_priority_fields_cannot_turn_unrelated_paragraph_into_confirmation(self):
        data = raw_result(self.docs)
        fact = data["categories"][0]["facts"][1]
        fact["condition_fields"] = {"applicability": "급여이체", "period_definition": "", "exceptions": "미확인"}
        fact["evidence"] = [{"document_id": "D001", "block_id": self.docs[0]["blocks"][0]["id"]}]
        result = product.validate_response(data, self.name, self.docs)
        checked = result["categories"][0]["facts"][1]
        self.assertEqual(checked["status"], "needs_review")
        self.assertEqual(checked["condition_fields_missing"], ["period_definition", "exceptions"])
        self.assertEqual(checked["condition_fields_unverified"], ["applicability"])
        self.assertTrue(any("기간 정의" in note for note in checked["important_conditions"]))
        fact["condition_fields"].update(period_definition="가입 전", exceptions="조건별 제한")
        checked = product.validate_response(data, self.name, self.docs)["categories"][0]["facts"][1]
        self.assertEqual(checked["status"], "needs_review")
        self.assertEqual(checked["condition_fields_missing"], [])
        self.assertEqual(checked["condition_fields_unverified"], list(product.CONDITION_FIELDS))

    def test_literal_field_matches_do_not_automatically_confirm_semantics(self):
        data = raw_result(self.docs)
        fact = data["categories"][0]["facts"][1]
        fact["condition_fields"] = {"applicability": "급여이체", "period_definition": "6개월",
                                    "exceptions": "중도해지 시 적용하지 않습니다"}
        checked = product.validate_response(data, self.name, self.docs)["categories"][0]["facts"][1]
        self.assertEqual(checked["condition_fields_missing"], [])
        self.assertEqual(checked["condition_fields_unverified"], [])
        self.assertEqual(checked["status"], "needs_review")

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
        self.assertEqual(good["documents"][0]["date"], "미확인")
        self.assertEqual(good["documents"][0]["date_status"], "needs_review")
        self.assertEqual(good["documents"][0]["date_evidence"][0]["document_id"], "D001")
        data["document_dates"][0]["date"] = "2027년 1월 1일"
        bad = product.validate_response(data, self.name, self.docs)
        self.assertEqual(bad["processing_status"], "partial")
        self.assertEqual(bad["documents"][0]["date"], "미확인")
        self.assertEqual(bad["documents"][0]["date_status"], "validation_failed")
        self.assertEqual(bad["documents"][0]["date_evidence"], [])
        self.assertNotIn("기간별 기본금리", bad["categories"][0]["summary"])

    def test_date_uses_selected_source_block_and_does_not_trust_legacy_quote(self):
        data = raw_result(self.docs)
        data["document_dates"][0]["evidence"][0]["quote"] = "모델이 임의로 만든 날짜 문구"
        result = product.validate_response(data, self.name, self.docs)
        date = result["documents"][0]
        self.assertEqual(date["date_status"], "needs_review")
        self.assertEqual(date["date"], "미확인")
        self.assertIn("2026년 1월 1일", date["date_evidence"][0]["quote"])
        self.assertNotIn("모델이 임의로 만든 날짜 문구", json.dumps(result, ensure_ascii=False))

    def test_inconsistent_model_descriptions_are_not_displayed_as_facts(self):
        data = raw_result(self.docs)
        fact = data["categories"][2]["facts"][0]
        fact["statement"] = "가입일에 고시된 이율을 적용합니다."
        fact["important_conditions"] = ["해지일에 고시된 이율을 적용합니다."]
        data["categories"][0]["unknowns"] = ["중도해지 우대 적용 여부가 미확인입니다."]
        data["categories"][1]["unknowns"] = ["모든 수수료가 무료입니다."]
        result = product.validate_response(data, self.name, self.docs)
        displayed = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["processing_status"], "complete")
        self.assertNotIn("가입일에 고시된 이율을 적용합니다.", displayed)
        self.assertNotIn("해지일에 고시된 이율을 적용합니다.", displayed)
        self.assertNotIn("중도해지 우대 적용 여부가 미확인입니다.", displayed)
        self.assertNotIn("모든 수수료가 무료입니다.", displayed)
        self.assertEqual(result["categories"][2]["facts"][0]["status"], "needs_review")
        self.assertTrue(result["categories"][2]["facts"][0]["evidence"])

    def test_document_date_quote_does_not_prove_what_the_date_means(self):
        data = raw_result(self.docs)
        date_block = next(b for b in self.docs[0]["blocks"] if "기준일" in b["text"])
        data["document_dates"][0]["evidence"][0]["quote"] = date_block["text"]
        result = product.validate_response(data, self.name, self.docs)
        self.assertEqual(result["documents"][0]["date"], "미확인")
        self.assertEqual(result["documents"][0]["date_status"], "needs_review")
        self.assertTrue(result["documents"][0]["date_evidence"])

    def table_response(self, text, quote, category=0, conditions=None):
        name, docs = product.prepare_documents("테스트 정기예금", [make_doc(text)])
        block = next(b for b in docs[0]["blocks"] if quote in b["text"])
        raw = raw_result(self.docs)
        raw["document_dates"] = [{"document_id": "D001", "date": "", "status": "not_found_in_input", "evidence": []}]
        for item in raw["categories"]:
            item["facts"] = []
            item["unknowns"] = []
        raw["categories"][category]["facts"] = [{"statement": "잘못 연결한 설명",
            "important_conditions": conditions or [], "conditions": [], "status": "found",
            "evidence": [{"document_id": "D001", "block_id": block["id"], "quote": quote}]}]
        return product.validate_response(raw, name, docs)

    def test_basic_table_values_follow_actual_columns_not_model_mapping(self):
        header = "구분 | 3개월 이상 9개월 미만 | 9개월 이상 18개월 이하"
        row = "기본금리 | 연 7.30% | 연 4.10%"
        result = self.table_response(header + "\n\n" + row, row, conditions=["허위 예외"])
        fact = result["categories"][0]["facts"][0]
        self.assertEqual(result["processing_status"], "complete")
        self.assertEqual(fact["status"], "found")
        self.assertEqual(fact["completeness"], "needs_review")
        self.assertEqual([(v["period"], v["rate"]) for v in fact["structured_values"]],
                         [("3개월 이상 9개월 미만", "연 7.30%"), ("9개월 이상 18개월 이하", "연 4.10%")])
        self.assertEqual([e["quote"] for e in fact["evidence"]], [header, row])
        self.assertNotIn("허위 예외", json.dumps(fact, ensure_ascii=False))
        self.assertIn("추가 확인", fact["important_conditions"][0])

    def test_contract_period_is_only_marked_when_the_source_says_so(self):
        header = "구분 | 12개월 이상 24개월 미만"
        row = "기본금리 | 연 3.20%"
        context = "기본금리 | • 신규일에 고시한 계약기간별 이자율 적용"
        fact = self.table_response(context + "\n\n" + header + "\n\n" + row, row)["categories"][0]["facts"][0]
        self.assertEqual(fact["structured_values"][0]["period_basis"], "contract_term")
        self.assertEqual(fact["evidence"][0]["quote"], context)
        without_context = self.table_response(header + "\n\n" + row, row)["categories"][0]["facts"][0]
        self.assertNotIn("period_basis", without_context["structured_values"][0])

    def test_display_highlights_require_explicit_source_and_safe_scope(self):
        text = ("⋅만기 전 해지할 경우 약정한 이율보다 낮은 중도해지이율이 적용됩니다.\n\n"
                "• 우대이율은 계약기간 만기 전 중도해지한 계좌에 대해서는 적용하지 않음")
        name, docs = product.prepare_documents("테스트 정기예금", [make_doc(text)])
        result = {"processing_status": "complete", "categories": [{"category": c} for c in product.CATEGORIES]}
        with patch("product_analysis.call_model", side_effect=AssertionError("API 호출 금지")):
            product.add_display_highlights(result, docs)
        self.assertEqual(len(result["categories"][0]["display_highlights"]), 1)
        self.assertEqual(len(result["categories"][2]["display_highlights"]), 1)
        for category in result["categories"]:
            for highlight in category.get("display_highlights", []):
                ev = highlight["evidence"][0]
                self.assertEqual(text[ev["start"]:ev["end"]], ev["quote"])
        _, reversed_docs = product.prepare_documents("테스트 정기예금", [make_doc(text.replace("적용됩니다.", "적용되지 않습니다.").replace("적용하지 않음", "적용함"))])
        other = {"processing_status": "complete", "categories": [{"category": c} for c in product.CATEGORIES]}
        product.add_display_highlights(other, reversed_docs)
        self.assertTrue(all("display_highlights" not in c for c in other["categories"]))
        product.add_display_highlights(other, [*docs, *docs])
        self.assertTrue(all("display_highlights" not in c for c in other["categories"]))

    def test_conditional_fee_highlight_requires_conditions_and_preserves_numbers(self):
        first = ("위법계약 해지권 • 은행이 금융소비자 보호에 관한 법률*을 위반하여 계약을 체결한 경우, "
                 "위반사항을 안 날로부터 2년 이내 (단, 계약체결일로부터 7년 이내이고 계약이 종료하기 전일 것)")
        second = ("은행은 해지를 요구받은 날로부터 20일 이내에 수락 여부를 통지하거나 거절 사유와 함께 거절을 통지하고, "
                  "정당한 사유 없이 금융소비자의 계약해지 요구를 따르지 않을 경우 해당 계약을 해지할 수 있으며, "
                  "이에 따라 계약이 해지될 경우 수수료, 위약금 등 비용을 요구할 수 없습니다.")
        name, docs = product.prepare_documents("테스트 정기예금", [make_doc(first + "\n\n" + second)])
        result = {"processing_status": "complete", "categories": [{"category": c} for c in product.CATEGORIES]}
        product.add_display_highlights(result, docs)
        highlight = result["categories"][1]["display_highlights"][0]
        self.assertIn("2년 이내", highlight["conditions"][1])
        self.assertIn("7년 이내", highlight["conditions"][1])
        self.assertIn("20일 이내", highlight["conditions"][2])
        self.assertIn("정당한 사유 없이", highlight["conditions"][3])
        self.assertEqual([e["quote"] for e in highlight["evidence"]], [first, second])
        _, negative_docs = product.prepare_documents("테스트 정기예금", [make_doc((first + "\n\n" + second).replace("요구할 수 없습니다", "요구할 수 있습니다"))])
        negative = {"processing_status": "complete", "categories": [{"category": c} for c in product.CATEGORIES]}
        product.add_display_highlights(negative, negative_docs)
        self.assertNotIn("display_highlights", negative["categories"][1])

    def test_disconnected_or_misaligned_periods_and_rates_are_not_linked(self):
        row = "기본금리 | 연 7.30% | 연 4.10%"
        for text in (
            "구분 | 3개월 이상 9개월 미만\n\n" + row,
            "구분 | 3개월 이상 9개월 미만 | 9개월 이상 18개월 이하\n\n별도의 문단\n\n" + row,
            "기간 3개월 이상 9개월 미만\n\n" + row,
        ):
            with self.subTest(text=text):
                result = self.table_response(text, row)
                fact = result["categories"][0]["facts"][0]
                self.assertEqual(fact["status"], "needs_review")
                self.assertNotIn("structured_values", fact)
                self.assertNotIn("3개월 이상 9개월 미만: 연 7.30%", fact["statement"])

    def test_early_rate_minimum_is_bound_to_its_own_row(self):
        header = "보유기간 | 이율 | 비고"
        row1 = "2개월 미만 | 연 0.2%"
        row2 = "2개월 이상 ~ 8개월 미만 | 기본이율×40%×보유일수/계약일수 | 최저이율 연 0.25%"
        result = self.table_response(header + "\n\n" + row1 + "\n\n" + row2, row2, category=2)
        fact = result["categories"][2]["facts"][0]
        self.assertEqual(fact["status"], "found")
        self.assertEqual(fact["structured_values"], [{"kind": "early_rate", "period": "2개월 이상 ~ 8개월 미만",
            "rate": "기본이율×40%×보유일수/계약일수", "minimum": "최저이율 연 0.25%"}])
        self.assertNotIn("2개월 미만", fact["statement"])
        self.assertEqual([e["quote"] for e in fact["evidence"]], [header, row2])

    def test_structured_table_is_source_derived_and_does_not_validate_model_claim(self):
        header = "구분 | 3개월 이상 9개월 미만"
        row = "기본금리 | 연 7.30%"
        name, docs = product.prepare_documents("테스트 정기예금", [make_doc(header + "\n\n" + row)])
        raw = raw_result(self.docs)
        raw["document_dates"] = [{"document_id": "D001", "date": "", "status": "not_found_in_input", "evidence": []}]
        for category in raw["categories"]:
            category["facts"] = []
        raw["categories"][0]["facts"] = [{"statement": "관계없는 금리는 연 99%", "important_conditions": [], "conditions": [], "status": "found",
            "evidence": [{"document_id": "D001", "block_id": "P0002", "quote": row + " 변조"}]}]
        result = product.validate_response(raw, name, docs)
        checked = result["categories"][0]["facts"][0]
        self.assertEqual(result["processing_status"], "complete")
        self.assertEqual(checked["status"], "found")
        self.assertEqual(checked["claim_status"], "needs_review")
        self.assertEqual(checked["structured_values"][0]["rate"], "연 7.30%")
        self.assertNotIn("99%", json.dumps(checked, ensure_ascii=False))
        self.assertNotIn("변조", json.dumps(checked, ensure_ascii=False))

    def test_incomplete_priority_does_not_hide_independently_verified_base_table(self):
        header = "구분 | 3개월 이상 9개월 미만"
        row = "기본금리 | 연 7.30%"
        name, docs = product.prepare_documents("테스트 정기예금", [make_doc(header + "\n\n" + row)])
        raw = raw_result(self.docs)
        raw["document_dates"] = [{"document_id": "D001", "date": "", "status": "not_found_in_input", "evidence": []}]
        for category in raw["categories"]:
            category["facts"] = []
        raw["categories"][0]["facts"] = [{"statement": "기본금리와 우대금리 조건", "important_conditions": [], "conditions": [],
            "condition_fields": {"applicability": "", "period_definition": "", "exceptions": ""},
            "status": "found", "evidence": [{"document_id": "D001", "block_id": "P0002"}]}]
        facts = product.validate_response(raw, name, docs)["categories"][0]["facts"]
        self.assertEqual([fact["status"] for fact in facts], ["found", "needs_review"])
        self.assertEqual(facts[0]["structured_values"][0]["rate"], "연 7.30%")
        self.assertEqual(facts[0]["claim_status"], "needs_review")
        self.assertEqual(facts[1]["condition_fields_missing"], list(product.CONDITION_FIELDS))

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
