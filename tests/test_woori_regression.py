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

    def test_saved_invalid_quote_and_date_remain_unconfirmed(self):
        self.assertEqual(self.before["categories"][0]["facts"][2]["status"], "validation_failed")
        self.assertEqual(self.after["categories"][0]["facts"][2]["status"], "validation_failed")
        self.assertEqual(self.after["categories"][0]["facts"][2]["evidence"], [])
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
        self.assertEqual(len(highlights), 3)
        for highlight in highlights:
            for evidence in highlight["evidence"]:
                self.assertEqual(self.source[evidence["start"]:evidence["end"]], evidence["quote"])


if __name__ == "__main__":
    unittest.main()
