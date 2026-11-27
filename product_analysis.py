"""Single-request deposit-product analysis, with local provenance verification."""
import json
import re
import urllib.request
from pathlib import Path

import analyze

CATEGORIES = ("금리와 우대조건", "수수료", "중도해지 조건")
MAX_DOCUMENT_CHARACTERS = 10000
OUTPUT_TOKENS = 8192
MAX_DOCUMENTS = 6
TOKEN_SAFETY_MARGIN = 2048

RULES_DIR = Path(__file__).resolve().parent / "rules"
RULE_FILES = (("common.md", "COM", 37), ("interest.md", "INT", 14),
              ("fees.md", "FEE", 13), ("termination.md", "TERM", 12))
RULE_ID_PATTERN = re.compile(r"(?m)^- \*\*([A-Z]+-\d{3}) \| [^\n]+?\*\*:")
# Official documented context window and max output tokens. Unknown models fail
# closed until their limits are confirmed rather than assuming a context size.
MODEL_LIMITS = {
    "gpt-4.1-mini": (1_047_576, 32_768),
    "gpt-4.1-mini-2025-04-14": (1_047_576, 32_768),
    "gpt-4.1": (1_047_576, 32_768),
    "gpt-4.1-2025-04-14": (1_047_576, 32_768),
    "gpt-4o-mini": (128_000, 16_384),
    "gpt-4o-mini-2024-07-18": (128_000, 16_384),
    "gpt-4o": (128_000, 16_384),
    "gpt-4o-2024-08-06": (128_000, 16_384),
    "gpt-4o-2024-11-20": (128_000, 16_384),
}

OUTPUT_CONTRACT = """[응답 스키마 적용]
지정된 JSON 스키마에 맞춰 세 항목을 각각 한 번씩 반환한다.
product의 suitability는 suitable, not_suitable, uncertain 중 하나이고,
consistency는 consistent, conflicting, uncertain 중 하나다.
document_dates에는 각 입력 문서 ID마다 날짜를 원문 그대로 적고 그 문서의 근거를 붙인다.
여러 기준일을 하나로 정할 수 없으면 needs_review로 표시한다.
날짜가 확인되지 않으면 date를 빈 문자열, evidence를 빈 배열로 둔다.
사용자가 입력한 날짜도 원문에 없으면 확정하지 않는다.
각 항목 summary는 조건을 빠뜨리지 않는 짧은 한국어로 쓴다.
사실마다 statement, important_conditions, conditions, status, evidence를 제공한다.
핵심 적용 기간·금액·횟수와 예외는 important_conditions에, 그 밖의 세부 조건은 conditions에 적는다.
확인하지 못한 사항은 unknowns에 적고 모든 근거에 document_id, block_id, quote를 넣는다."""


class RuleConfigurationError(Exception):
    pass


def instruction_text():
    sections = []
    seen = set()
    for filename, prefix, count in RULE_FILES:
        try:
            content = (RULES_DIR / filename).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RuleConfigurationError("규칙 파일을 읽을 수 없습니다.") from exc
        ids = RULE_ID_PATTERN.findall(content)
        expected = {f"{prefix}-{number:03d}" for number in range(1, count + 1)}
        if (not content.startswith("# ") or len(ids) != count
                or set(ids) != expected or seen.intersection(ids)):
            raise RuleConfigurationError("규칙 파일의 제목 또는 규칙 ID를 확인해 주세요.")
        seen.update(ids)
        sections.append(f"[규칙 파일: {filename}]\n{content.rstrip()}")
    return "\n\n".join([*sections, OUTPUT_CONTRACT])


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
EVIDENCE_SCHEMA = obj({"document_id": STRING, "block_id": STRING, "quote": STRING})
FACT_SCHEMA = obj({
    "statement": STRING,
    "important_conditions": {"type": "array", "items": STRING},
    "conditions": {"type": "array", "items": STRING},
    "status": {"type": "string", "enum": ["found", "not_found_in_input", "needs_review"]},
    "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
})
SCHEMA = obj({
    "product": obj({
        "suitability": {"type": "string", "enum": ["suitable", "not_suitable", "uncertain"]},
        "consistency": {"type": "string", "enum": ["consistent", "conflicting", "uncertain"]},
        "note": STRING,
    }),
    "document_dates": {"type": "array", "items": obj({
        "document_id": STRING,
        "date": STRING,
        "status": {"type": "string", "enum": ["found", "not_found_in_input", "needs_review"]},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
    })},
    "categories": {"type": "array", "items": obj({
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "summary": STRING,
        "facts": {"type": "array", "items": FACT_SCHEMA},
        "unknowns": {"type": "array", "items": STRING},
    })},
})


class InputError(ValueError):
    pass


class ModelError(Exception):
    pass


def normalized_with_offsets(source):
    result, boundaries, i = [], [0], 0
    while i < len(source):
        if source[i] == "\r":
            result.append("\n")
            i += 2 if source[i:i+2] == "\r\n" else 1
        else:
            result.append(source[i])
            i += 1
        boundaries.append(i)
    return "".join(result), boundaries


def prepare_documents(product_name, documents):
    if not isinstance(product_name, str) or not product_name.strip() or len(product_name) > 100:
        raise InputError("분석할 상품명을 입력해 주세요.")
    product_name = product_name.strip()
    if not isinstance(documents, list) or not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise InputError("같은 상품의 문서를 1개에서 6개까지 입력해 주세요.")
    prepared = []
    total_characters = 0
    for number, document in enumerate(documents, 1):
        if not isinstance(document, dict):
            raise InputError("문서 정보를 확인해 주세요.")
        name, product = document.get("name"), document.get("product_name")
        scope, date, source = document.get("scope"), document.get("date"), document.get("text")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise InputError("각 문서의 이름을 입력해 주세요.")
        if not isinstance(product, str) or product.strip() != product_name:
            raise InputError("문서별 상품명이 분석할 상품명과 같아야 합니다. 다른 상품은 따로 분석해 주세요.")
        if scope not in ("full", "excerpt", "unknown"):
            raise InputError("각 문서의 입력 범위를 선택해 주세요.")
        if not isinstance(date, str) or len(date) > 100:
            raise InputError("문서 기준일·개정일 입력을 확인해 주세요.")
        if not isinstance(source, str) or not source.strip():
            raise InputError("각 문서의 본문을 입력해 주세요.")
        total_characters += len(source)
        if total_characters > MAX_DOCUMENT_CHARACTERS:
            raise InputError("모든 문서 본문의 합계가 10,000자를 초과했습니다. 입력을 줄여 다시 시도해 주세요.")
        normalized, boundaries = normalized_with_offsets(source)
        cursor, blocks = 0, []
        for paragraph in analyze.paragraphs(normalized):
            start = normalized.find(paragraph["text"], cursor)
            end = start + len(paragraph["text"])
            first_line = paragraph["text"].split("\n", 1)[0].strip()
            heading = first_line if len(first_line) <= 100 and re.match(r"^(제\s*\d+\s*조|[■▶◆]|\d+[.．])", first_line) else ""
            blocks.append({"id": paragraph["id"], "heading": heading, "text": paragraph["text"],
                           "start": start, "end": end})
            cursor = end
        prepared.append({"id": f"D{number:03d}", "name": name.strip(), "product_name": product_name,
                         "scope": scope, "date": date.strip() or "미확인",
                         "text": normalized, "blocks": blocks, "_source": source, "_boundaries": boundaries})
    return product_name, prepared


def build_payload(product_name, documents, model):
    if sum(len(doc["_source"]) for doc in documents) > MAX_DOCUMENT_CHARACTERS:
        raise InputError("모든 문서 본문의 합계가 10,000자를 초과했습니다.")
    limits = MODEL_LIMITS.get(model)
    if limits is None:
        raise InputError("선택한 모델의 토큰 한도를 확인할 수 없습니다. 지원 모델 설정을 확인해 주세요.")
    public_docs = [{k: v for k, v in doc.items() if not k.startswith("_")} for doc in documents]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instruction_text()},
            {"role": "user", "content": json.dumps({"product_name": product_name, "documents": public_docs}, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "deposit_analysis", "strict": True, "schema": SCHEMA}},
        "max_tokens": OUTPUT_TOKENS,
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # Conservative UTF-8 byte estimate includes rules, schema and all documents;
    # reserve message overhead and output capacity without silently truncating.
    context_limit, output_limit = limits
    if OUTPUT_TOKENS > output_limit or len(encoded) + TOKEN_SAFETY_MARGIN + OUTPUT_TOKENS > context_limit:
        raise InputError("선택한 모델의 토큰 한도를 초과합니다. 문서를 줄여 다시 시도해 주세요.")
    return encoded


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call_model(encoded, key):
    request = urllib.request.Request("https://api.openai.com/v1/chat/completions",
        data=encoded, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    # No SDK retry, redirect, repair call or per-section request.
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=120) as response:
        body = json.load(response)
    try:
        choice = body["choices"][0]
        message = choice["message"]
        if choice["finish_reason"] != "stop" or not isinstance(message, dict) or message.get("refusal"):
            raise ModelError()
        return json.loads(message["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ModelError() from None


def exact_keys(value, keys):
    return isinstance(value, dict) and set(value) == set(keys)


PERIOD_CELL = re.compile(r"^\d+\s*(?:일|개월|년)\s*(?:이상|초과|이하|미만)(?:\s*~?\s*\d+\s*(?:일|개월|년)\s*(?:이상|초과|이하|미만))?$")
RATE_CELL = re.compile(r"^(?:연\s*)?\d+(?:\.\d+)?\s*%$")
EARLY_FORMULA = re.compile(r"^기본이율\s*×\s*\d+(?:\.\d+)?\s*%\s*×\s*보유일수\s*/\s*계약일수$")
MINIMUM_CELL = re.compile(r"^최저이율\s*연\s*\d+(?:\.\d+)?\s*%$")


def source_evidence(doc, block, start=0, end=None):
    """Return an exact source span, including offsets for the original line endings."""
    if end is None:
        end = len(block["text"])
    start, end = block["start"] + start, block["start"] + end
    original_start, original_end = doc["_boundaries"][start], doc["_boundaries"][end]
    return {"document_id": doc["id"], "document_name": doc["name"], "block_id": block["id"],
            "quote": doc["_source"][original_start:original_end], "start": original_start, "end": original_end}


DISPLAY_SOURCE_PATTERNS = (
    (CATEGORIES[0], re.compile(
        r"(?m)^[ \t]*[•⋅·-]?[ \t]*(우대(?:이율|금리)(?:은|는)\s*(?:계약기간\s*)?만기\s*전\s*중도해지한\s*계좌에\s*대해서는\s*적용하지\s*않음)[ \t]*$"),
     "만기 전에 해지한 계좌에는 우대금리가 적용되지 않아요."),
    (CATEGORIES[2], re.compile(
        r"(?m)^[ \t]*[•⋅·-]?[ \t]*(만기\s*전\s*해지할\s*경우\s*약정한\s*(?:이율|금리)보다\s*낮은\s*중도해지(?:이율|금리|이자율)이\s*적용됩니다\.)[ \t]*$"),
     "만기 전에 해지하면 약속한 금리보다 낮은 금리가 적용돼요."),
)

ILLEGAL_FEE_CONTEXT = re.compile(
    r"위법계약.*?은행이\s*금융소비자\s*보호에\s*관한\s*법률\*을\s*위반하여\s*계약을\s*체결한\s*(?:해지권\s*)?경우"
    r".*?위반사항을\s*안\s*날로부터\s*(\d+)\s*년\s*이내\s*\(단,\s*계약체결일로부터\s*(\d+)\s*년\s*이내이고\s*계약이\s*종료하기\s*전일\s*것\)", re.S)
ILLEGAL_FEE_RULE = re.compile(
    r"요구받은\s*날로부터\s*(\d+)\s*일\s*이내.*?수락\s*여부를\s*통지하거나\s*거절\s*사유와\s*함께\s*거절을\s*통지"
    r".*?정당한\s*사유\s*없이\s*금융소비자의\s*계약해지\s*요구를\s*따르지\s*않을\s*경우"
    r".*?이에\s*따라\s*계약이\s*해지될\s*경우.*?수수료,\s*위약금\s*등\s*비용을\s*요구할\s*수\s*없습니다\.", re.S)


def add_display_highlights(result, documents):
    """Add source-backed display hints without changing model extraction or evidence checks."""
    if result["processing_status"] == "blocked" or len(documents) != 1:
        return result
    document = documents[0]
    for category_name, pattern, statement in DISPLAY_SOURCE_PATTERNS:
        for block in document["blocks"]:
            match = pattern.search(block["text"])
            if match:
                category = next(item for item in result["categories"] if item["category"] == category_name)
                category.setdefault("display_highlights", []).append({
                    "statement": statement,
                    "evidence": [source_evidence(document, block, *match.span(1))],
                })
                break
    # This applies only where the source itself contains both the legal
    # preconditions and the specific cost restriction in adjacent blocks.
    for first, second in zip(document["blocks"], document["blocks"][1:]):
        context = ILLEGAL_FEE_CONTEXT.search(first["text"])
        rule = ILLEGAL_FEE_RULE.search(second["text"])
        if context and rule and "해지권" in first["text"]:
            category = next(item for item in result["categories"] if item["category"] == CATEGORIES[1])
            category.setdefault("display_highlights", []).append({
                "statement": "위법계약 해지에는 정해진 조건에 따른 비용 제한이 있어요.",
                "conditions": [
                    "은행이 금융소비자 보호에 관한 법률을 위반해 계약을 체결한 경우에 해당해요.",
                    f"위반을 안 날부터 {context[1]}년 이내, 계약일부터 {context[2]}년 이내이고 계약이 끝나기 전이어야 해요.",
                    f"은행은 요구받은 날부터 {rule[1]}일 이내에 수락 여부를 알리거나 거절 사유와 함께 거절해야 해요.",
                    "은행이 정당한 사유 없이 해지 요구를 따르지 않아 계약을 해지하면 수수료·위약금 등 비용을 요구할 수 없어요.",
                ],
                "evidence": [source_evidence(document, first), source_evidence(document, second)],
            })
            break
    return result


def structured_tables(documents):
    """Recognize only explicit, contiguous table layouts; never join loose tokens."""
    rows = []
    for doc in documents:
        blocks = doc["blocks"]
        for i, header in enumerate(blocks):
            headers = [s.strip() for s in header["text"].strip().split("|")]
            if (len(headers) > 1 and headers[0] == "구분" and
                    all(PERIOD_CELL.fullmatch(cell) for cell in headers[1:]) and i + 1 < len(blocks)):
                row = blocks[i + 1]
                cells = [s.strip() for s in row["text"].strip().split("|")]
                if (len(cells) == len(headers) and cells[0] in ("기본금리", "기본이율") and
                        all(RATE_CELL.fullmatch(cell) for cell in cells[1:])):
                    context = next((b for b in reversed(blocks[max(0, i - 3):i])
                                    if re.search(r"(?m)^[ \t]*기본(?:금리|이율)[ \t]*\|[^\n]*계약기간별[ \t]*이자율[ \t]*적용[ \t]*$", b["text"])), None)
                    values = [{"kind": "basic_rate", "period": period, "rate": rate}
                              for period, rate in zip(headers[1:], cells[1:])]
                    if context:
                        for value in values:
                            value["period_basis"] = "contract_term"
                    rows.append({"category": CATEGORIES[0], "document_id": doc["id"], "block_id": row["id"],
                                 "match": row["text"].strip(), "values": values,
                                 "header": source_evidence(doc, header),
                                 "context": source_evidence(doc, context) if context else None})
            if headers != ["보유기간", "이율", "비고"]:
                continue
            # Adjacent blocks/lines only: a new paragraph that is not a table
            # row ends this table. Each period, formula and minimum share a row.
            for block in blocks[i + 1:]:
                parsed = []
                for line in block["text"].splitlines():
                    cells = [s.strip() for s in line.strip().split("|")]
                    if (len(cells) not in (2, 3) or not PERIOD_CELL.fullmatch(cells[0]) or
                            not (RATE_CELL.fullmatch(cells[1]) or EARLY_FORMULA.fullmatch(cells[1])) or
                            (len(cells) == 3 and not MINIMUM_CELL.fullmatch(cells[2]))):
                        parsed = []
                        break
                    value = {"kind": "early_rate", "period": cells[0], "rate": cells[1]}
                    if len(cells) == 3:
                        value["minimum"] = cells[2]
                    parsed.append((line.strip(), value))
                if not parsed:
                    break
                for line, value in parsed:
                    rows.append({"category": CATEGORIES[2], "document_id": doc["id"], "block_id": block["id"],
                                 "match": line, "values": [value], "header": source_evidence(doc, header)})
    return rows


def extract_supported_table_values(category, verified, tables):
    """Use a table row only when the AI's validated quotation covers that row."""
    matched = []
    for table in tables:
        if table["category"] != category:
            continue
        if any(ev["document_id"] == table["document_id"] and ev["block_id"] == table["block_id"]
               and table["match"] in ev["quote"] for ev in verified):
            matched.append(table)
    if not matched:
        return None
    values = [value for table in matched for value in table["values"]]
    description = "; ".join(f'{v["period"]}: {v["rate"]}' +
                            (f' ({v["minimum"]})' if v.get("minimum") else "") for v in values)
    headers = []
    for table in matched:
        if table.get("context") and table["context"] not in headers and table["context"] not in verified:
            headers.append(table["context"])
        if table["header"] not in headers and table["header"] not in verified:
            headers.append(table["header"])
    return {"statement": "원문 표: " + description,
            "important_conditions": ["기준일과 다른 적용 조건·예외는 원문에서 추가 확인해 주세요."],
            "conditions": [], "status": "found", "evidence": headers + verified,
            "structured_values": values, "completeness": "needs_review"}


def validate_response(raw, product_name, documents):
    if not exact_keys(raw, ("product", "document_dates", "categories")):
        raise ModelError()
    product, categories = raw["product"], raw["categories"]
    if (not exact_keys(product, ("suitability", "consistency", "note"))
            or product["suitability"] not in ("suitable", "not_suitable", "uncertain")
            or product["consistency"] not in ("consistent", "conflicting", "uncertain")
            or not isinstance(product["note"], str)
            or not isinstance(categories, list) or len(categories) != len(CATEGORIES)):
        raise ModelError()
    if any(not exact_keys(c, ("category", "summary", "facts", "unknowns")) for c in categories):
        raise ModelError()
    names = [c["category"] for c in categories]
    if any(not isinstance(n, str) for n in names) or set(names) != set(CATEGORIES):
        raise ModelError()
    # Do not display combined findings when the model identifies unrelated products.
    blocked = (product["consistency"] == "conflicting" or product["suitability"] == "not_suitable"
               or (len(documents) > 1 and product["consistency"] == "uncertain"))
    index = {(doc["id"], b["id"]): (doc, b) for doc in documents for b in doc["blocks"]}
    dates = raw["document_dates"]
    if not isinstance(dates, list) or len(dates) != len(documents):
        raise ModelError()
    if any(not exact_keys(d, ("document_id", "date", "status", "evidence"))
           or not isinstance(d["document_id"], str) for d in dates):
        raise ModelError()
    if set(d["document_id"] for d in dates) != set(d["id"] for d in documents):
        raise ModelError()
    metadata, bad_date = [], False
    for doc in documents:
        date = next(d for d in dates if d["document_id"] == doc["id"])
        if (not isinstance(date["date"], str) or not isinstance(date["evidence"], list)
                or date["status"] not in ("found", "not_found_in_input", "needs_review")):
            raise ModelError()
        valid_date, date_invalid, date_evidence = False, False, []
        for ev in date["evidence"]:
            if not exact_keys(ev, ("document_id", "block_id", "quote")) or any(not isinstance(ev[k], str) for k in ev):
                raise ModelError()
            match = index.get((ev["document_id"], ev["block_id"]))
            if (match and ev["document_id"] == doc["id"] and ev["quote"].strip()
                    and ev["quote"] in match[1]["text"] and date["date"].strip()
                    and date["date"] in ev["quote"]):
                valid_date = True
                source_doc, block = match
                start = block["start"] + block["text"].index(ev["quote"])
                end = start + len(ev["quote"])
                original_start, original_end = source_doc["_boundaries"][start], source_doc["_boundaries"][end]
                date_evidence.append({"document_id": doc["id"], "document_name": doc["name"], "block_id": block["id"],
                                      "quote": doc["_source"][original_start:original_end],
                                      "start": original_start, "end": original_end})
            else:
                date_invalid = True
        if date["status"] == "found" and (date_invalid or not valid_date or not date["evidence"]):
            bad_date = date_invalid = True
        elif date["status"] != "found" and (date["date"] or date["evidence"]):
            raise ModelError()
        # Exact inclusion does not establish whether a date is a rate date,
        # contract date or approval period. Do not label it as verified.
        metadata.append({"id": doc["id"], "name": doc["name"], "scope": doc["scope"],
                         "date": "미확인",
                         "date_status": "validation_failed" if date_invalid else
                                        "needs_review" if date["status"] == "found" else date["status"],
                         "date_evidence": [] if date_invalid else date_evidence})
    tables = structured_tables(documents)
    partial = bad_date
    output = []
    for name in CATEGORIES:
        category = next(c for c in categories if c["category"] == name)
        if (not isinstance(category["summary"], str) or not isinstance(category["facts"], list)
                or not isinstance(category["unknowns"], list)
                or any(not isinstance(x, str) for x in category["unknowns"])):
            raise ModelError()
        facts, bad, has_grounded, has_structured = [], False, False, False
        for fact in category["facts"]:
            if (not exact_keys(fact, ("statement", "important_conditions", "conditions", "status", "evidence"))
                    or not isinstance(fact["statement"], str) or not fact["statement"].strip()
                    or not isinstance(fact["important_conditions"], list) or any(not isinstance(x, str) for x in fact["important_conditions"])
                    or not isinstance(fact["conditions"], list) or any(not isinstance(x, str) for x in fact["conditions"])
                    or fact["status"] not in ("found", "not_found_in_input", "needs_review")
                    or not isinstance(fact["evidence"], list)):
                raise ModelError()
            if fact["status"] == "not_found_in_input" and fact["evidence"]:
                raise ModelError()
            verified, invalid = [], False
            for ev in fact["evidence"]:
                if not exact_keys(ev, ("document_id", "block_id", "quote")) or any(not isinstance(ev[k], str) for k in ev):
                    raise ModelError()
                match = index.get((ev["document_id"], ev["block_id"]))
                quote = ev["quote"]
                if not match or not quote.strip() or quote not in match[1]["text"]:
                    invalid = True
                    continue
                doc, block = match
                start = block["start"] + block["text"].index(quote)
                end = start + len(quote)
                original_start, original_end = doc["_boundaries"][start], doc["_boundaries"][end]
                verified.append({"document_id": doc["id"], "document_name": doc["name"], "block_id": block["id"],
                                 "quote": doc["_source"][original_start:original_end],
                                 "start": original_start, "end": original_end})
            if invalid or (fact["status"] == "found" and not verified):
                bad = partial = True
                facts.append({"statement": "근거 검증에 실패해 이 사실을 표시하지 않습니다.",
                              "important_conditions": [], "conditions": [], "status": "validation_failed", "evidence": []})
            else:
                has_grounded = has_grounded or bool(verified)
                structured = extract_supported_table_values(name, verified, tables) if fact["status"] == "found" else None
                if structured:
                    facts.append(structured)
                    has_structured = True
                else:
                    facts.append({"statement": "원문 발췌: " + verified[0]["quote"] if verified else
                                               "입력에서 관련 내용과 조건을 추가 확인해 주세요.",
                                  "important_conditions": ["관련 조건과 예외가 모두 포함됐는지 추가 확인해 주세요."] if verified else [],
                                  "conditions": [], "status": "needs_review", "evidence": verified})
        if blocked:
            output.append({"category": name, "summary": "분석 결과를 표시하지 않습니다.", "facts": [], "unknowns": []})
        else:
            summary = ("원문 표에서 확인한 값입니다. 적용 조건과 예외는 추가 확인해 주세요." if has_structured
                       else "일부 근거를 확인하지 못했습니다. 원문과 적용 조건을 추가 확인해 주세요." if bad
                       else "인용의 의미와 적용 조건을 원문에서 추가 확인해 주세요." if has_grounded
                       else "입력에서 관련 내용과 조건을 추가 확인해 주세요.")
            # Free-form unknowns can contradict a fact, another category, or
            # the input itself. Keep them only in the raw model response.
            unknowns = (["입력에서 확인되지 않은 내용과 적용 조건을 원문에서 추가 확인해 주세요."]
                        if category["unknowns"] or not facts else [])
            output.append({"category": name, "summary": summary, "facts": facts, "unknowns": unknowns})
    return {
        "processing_status": "blocked" if blocked else "partial" if partial else "complete",
        "product": {"name": product_name, **product, "note": "문서 내용과 적용 조건은 추가 확인이 필요합니다."},
        "documents": metadata,
        "categories": output,
    }
