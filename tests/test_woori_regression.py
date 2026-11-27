"""Offline regression using the optional locally stored real response."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import product_analysis as product


DATA = Path(__file__).resolve().parent.parent / "test_data" / "woori_first_deposit"
FILES = (DATA / "result_woori.json", DATA / "validated_woori.json", DATA / "input_verified.txt")


@unittest.skipUnless(all(path.is_file() for path in FILES), "로컬 검증 자료가 없습니다")
class SavedWooriResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        response = json.loads(FILES[0].read_text(encoding="utf-8"))
        cls.raw = json.loads(response["choices"][0]["message"]["content"])
        cls.before = json.loads(FILES[1].read_text(encoding="utf-8"))
        source = FILES[2].read_text(encoding="utf-8")
        cls.source = source
        name = cls.before["product"]["name"]
        doc = {"name": cls.before["documents"][0]["name"], "product_name": name,
               "scope": "full", "date": "", "text": source}
        _, docs = product.prepare_documents(name, [doc])
        with patch.object(product.urllib.request, "build_opener", side_effect=AssertionError("API 호출 금지")):
            cls.after = product.add_display_highlights(product.validate_response(cls.raw, name, docs), docs)

    def test_saved_quote_is_replaced_by_source_but_date_remains_unconfirmed(self):
        self.assertEqual(self.before["categories"][0]["facts"][2]["status"], "validation_failed")
        self.assertEqual(self.after["categories"][0]["facts"][2]["status"], "needs_review")
        self.assertEqual(self.after["categories"][0]["facts"][2]["evidence"][0]["block_id"], "P0018")
        self.assertEqual(self.after["categories"][0]["facts"][2]["condition_fields_missing"],
                         list(product.CONDITION_FIELDS))
        self.assertEqual(self.after["documents"][0]["date_status"], "validation_failed")
        self.assertEqual(self.after["documents"][0]["date"], "미확인")

    def test_unverified_omission_and_contradictions_do_not_become_confirmed(self):
        rates, fee, early = self.after["categories"]
        self.assertEqual(self.before["categories"][0]["facts"][3]["status"], "found")
        self.assertEqual(rates["facts"][3]["status"], "needs_review")
        self.assertTrue(rates["facts"][3]["evidence"])
        self.assertEqual(early["facts"][0]["status"], "needs_review")
        displayed = json.dumps(self.after, ensure_ascii=False)
        self.assertNotIn("중도해지 시점의 영업점", displayed)
        self.assertNotIn("중도해지 시 우대금리 적용 여부 명확히 명시되지 않음", displayed)
        self.assertNotIn("면제 조건에 관한 정보가 문서에 미기재됨", displayed)
        self.assertEqual(self.after["processing_status"], "partial")
        self.assertTrue(fee["unknowns"])

    def test_correct_source_rates_and_minimum_remain_available_without_free_fee_claim(self):
        rates, fee, early = self.after["categories"]
        rate_quotes = "\n".join(e["quote"] for e in rates["facts"][0]["evidence"])
        for value in ("2.60%", "2.80%", "2.20%"):
            self.assertIn(value, rate_quotes)
        for index in (1, 2, 3):
            self.assertIn("0.15%", "\n".join(e["quote"] for e in early["facts"][index]["evidence"]))
        self.assertEqual(rates["facts"][0]["status"], "found")
        self.assertEqual(rates["facts"][0]["completeness"], "needs_review")
        self.assertIn("6개월 이상 12개월 미만: 연 2.60%", rates["facts"][0]["statement"])
        self.assertTrue(any(e["block_id"] == "P0015" for e in rates["facts"][0]["evidence"]))
        self.assertTrue(all(v["period_basis"] == "contract_term" for v in rates["facts"][0]["structured_values"]))
        self.assertIn("기본이율×50%×보유일수/계약일수 (최저이율 연 0.15%)", early["facts"][1]["statement"])
        self.assertIn("적용되지 않아요", rates["display_highlights"][0]["statement"])
        self.assertIn("낮은 금리가 적용돼요", early["display_highlights"][0]["statement"])
        conditional_fee = fee["display_highlights"][0]
        self.assertEqual([e["block_id"] for e in conditional_fee["evidence"]], ["P0053", "P0054"])
        self.assertIn("정당한 사유 없이", conditional_fee["conditions"][3])
        self.assertIn("확인", " ".join(fee["unknowns"]))
        self.assertNotIn("무료", json.dumps(fee, ensure_ascii=False))

    def test_all_display_highlights_have_exact_source_offsets(self):
        highlights = [h for c in self.after["categories"] for h in c.get("display_highlights", [])]
        self.assertEqual(len(highlights), 4)
        period = next(h for h in highlights if "이자 계산 기간" in h["statement"])
        self.assertIn("중도해지일 전일까지", period["statement"])
        self.assertEqual([e["block_id"] for e in period["evidence"]], ["P0023", "P0024"])
        for highlight in highlights:
            for evidence in highlight["evidence"]:
                self.assertEqual(self.source[evidence["start"]:evidence["end"]], evidence["quote"])


FINAL = Path(__file__).resolve().parent.parent / "artifacts" / "final_validation_20261127"
FINAL_FILES = (FINAL / "result.json", FINAL / "validated_result.json", DATA / "input_verified.txt")

ID_RESPONSE = Path(__file__).resolve().parent.parent / "artifacts" / "id_schema_validation_20261127_01" / "result.json"


@unittest.skipUnless(ID_RESPONSE.is_file() and FILES[2].is_file(), "문단 ID 실제 응답 자료가 없습니다")
class IdResponseDisplayTests(unittest.TestCase):
    def setUp(self):
        envelope = json.loads(ID_RESPONSE.read_text(encoding="utf-8"))
        self.raw = json.loads(envelope["choices"][0]["message"]["content"])
        source = FILES[2].read_text(encoding="utf-8")
        name = "저장 응답 재생"
        _, self.docs = product.prepare_documents(name, [{"name": "원문", "product_name": name,
                                                        "scope": "full", "date": "", "text": source}])
        with patch.object(product.urllib.request, "build_opener", side_effect=AssertionError("API 호출 금지")):
            self.result = product.add_display_highlights(product.validate_response(self.raw, name, self.docs), self.docs)

    def test_incorrect_model_claims_are_not_displayed_as_facts(self):
        output = json.dumps(self.result, ensure_ascii=False)
        self.assertNotIn("우대조건을 모두 충족해야 한다", output)
        self.assertNotIn("입금일부터 해지일까지", output)
        priority = self.result["categories"][0]["facts"][-1]
        self.assertEqual(priority["status"], "needs_review")
        self.assertTrue(priority["condition_fields_unverified"])
        self.assertIn("중도해지일 전일까지", output)
        self.assertIn("0.15%", output)

    def test_unknown_date_is_invalid_even_if_word_occurs_in_source(self):
        source = "날짜 미확인"
        name = "테스트"
        _, docs = product.prepare_documents(name, [{"name": "문서", "product_name": name,
                                                    "scope": "full", "date": "", "text": source}])
        raw = {"product": {"suitability": "suitable", "consistency": "consistent", "note": ""},
               "document_dates": [{"document_id": "D001", "date": "미확인", "status": "found",
                                   "evidence": [{"document_id": "D001", "block_id": "P0001"}]}],
               "categories": [{"category": c, "summary": "", "facts": [], "unknowns": []} for c in product.CATEGORIES]}
        result = product.validate_response(raw, name, docs)
        self.assertEqual(result["documents"][0]["date_status"], "validation_failed")
        self.assertEqual(result["documents"][0]["date"], "미확인")
        self.assertEqual(result["processing_status"], "partial")

    def test_period_highlight_is_not_created_from_end_date_including_termination(self):
        source = "중도해지이율\n\n입금일부터 중도해지일까지의 기간에 대하여\n\n예치기간별 중도해지이율로 계산한 이자를 합계"
        name = "테스트"
        _, docs = product.prepare_documents(name, [{"name": "문서", "product_name": name,
                                                    "scope": "full", "date": "", "text": source}])
        result = {"processing_status": "partial", "categories": [{"category": c} for c in product.CATEGORIES]}
        product.add_display_highlights(result, docs)
        self.assertFalse(result["categories"][2].get("display_highlights"))


@unittest.skipUnless(all(path.is_file() for path in FINAL_FILES), "최종 실제 응답 자료가 없습니다")
class FinalResponseEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        envelope = json.loads(FINAL_FILES[0].read_text(encoding="utf-8"))
        raw = json.loads(envelope["choices"][0]["message"]["content"])
        cls.before = json.loads(FINAL_FILES[1].read_text(encoding="utf-8"))
        cls.source = FINAL_FILES[2].read_text(encoding="utf-8")
        name = cls.before["product"]["name"]
        document = {"name": cls.before["documents"][0]["name"], "product_name": name,
                    "scope": "full", "date": "", "text": cls.source}
        _, docs = product.prepare_documents(name, [document])
        with patch.object(product.urllib.request, "build_opener", side_effect=AssertionError("API 호출 금지")):
            cls.after = product.add_display_highlights(product.validate_response(raw, name, docs), docs)

    def test_four_legacy_quote_mismatches_link_to_real_paragraphs_not_confirmed_claims(self):
        positions = [(0, 1, ["P0018"]), (1, 0, ["P0054"]),
                     (2, 1, ["P0018"]), (2, 2, ["P0036", "P0037", "P0042"])]
        self.assertEqual(sum(f["status"] == "validation_failed" for c in self.before["categories"] for f in c["facts"]), 4)
        self.assertEqual(sum(f["status"] == "validation_failed" for c in self.after["categories"] for f in c["facts"]), 0)
        for category, index, expected_ids in positions:
            with self.subTest(category=category, index=index):
                self.assertEqual(self.before["categories"][category]["facts"][index]["status"], "validation_failed")
                fact = self.after["categories"][category]["facts"][index]
                self.assertEqual(fact["status"], "needs_review")
                self.assertEqual([e["block_id"] for e in fact["evidence"]], expected_ids)
                for ev in fact["evidence"]:
                    self.assertEqual(self.source[ev["start"]:ev["end"]], ev["quote"])
        self.assertNotIn("P0019", [e["block_id"] for e in self.after["categories"][0]["facts"][1]["evidence"]])
        self.assertEqual(self.after["categories"][0]["facts"][1]["condition_fields_missing"], list(product.CONDITION_FIELDS))
        self.assertEqual(self.after["documents"][0]["date_status"], "validation_failed")
        self.assertEqual(self.after["processing_status"], "partial")

    def test_other_confirmed_table_values_are_not_hidden(self):
        rate = self.after["categories"][0]["facts"][0]
        early = self.after["categories"][2]["facts"][0]
        self.assertEqual(rate["status"], "found")
        self.assertEqual([v["rate"] for v in rate["structured_values"]], ["연 2.60%", "연 2.80%", "연 2.20%"])
        self.assertEqual(early["status"], "found")
        self.assertEqual(len(early["structured_values"]), 5)
        self.assertNotIn("minimum", early["structured_values"][0])
        self.assertTrue(all("0.15%" in v.get("minimum", "") for v in early["structured_values"][1:]))


if __name__ == "__main__":
    unittest.main()
