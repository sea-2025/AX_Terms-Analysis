"use strict";

const categoryNames = ["금리와 우대조건", "수수료", "중도해지 조건"];
const form = document.getElementById("product-form");
const productName = document.getElementById("product-name");
const docs = document.getElementById("documents");
const submit = document.getElementById("product-submit");
const addDocument = document.getElementById("add-document");
const resultCards = document.getElementById("product-cards");
const inputPanel = document.getElementById("product-input");
const inputToggle = document.getElementById("product-input-toggle");
const workspace = document.getElementById("product-workspace");
const badge = document.getElementById("product-badge");
const alertBox = document.getElementById("product-alert");
const error = document.getElementById("product-error");
const status = document.getElementById("product-status");
let busy = false;

function el(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = content;
  return element;
}

function expanded(value) {
  inputPanel.hidden = !value;
  inputToggle.setAttribute("aria-expanded", String(value));
  inputToggle.textContent = value ? "입력 문서 접기" : "입력 문서 보기·수정";
  workspace.classList.toggle("result-focus", !value);
}

inputToggle.addEventListener("click", () => {
  expanded(inputPanel.hidden);
  if (!inputPanel.hidden) productName.focus();
});

function renumber() {
  [...docs.children].forEach((item, index) => {
    item.querySelector(".document-number").textContent = String(index + 1);
    item.querySelector(".remove-document").hidden = docs.children.length === 1;
  });
  addDocument.disabled = docs.children.length >= 6;
}

function appendDocument() {
  if (docs.children.length >= 6) return;
  const fragment = document.getElementById("document-template").content.cloneNode(true);
  const item = fragment.querySelector(".document-entry");
  item.querySelector(".remove-document").addEventListener("click", () => {
    item.remove();
    renumber();
  });
  docs.append(fragment);
  renumber();
}

addDocument.addEventListener("click", appendDocument);

const stateLabels = {
  found: "원문 근거 확인",
  not_found_in_input: "입력에서 확인되지 않음",
  needs_review: "추가 확인 필요",
  validation_failed: "근거 검증 실패"
};

function easyPeriod(period) {
  return period.replace(/(\d+)\s*개월/g, (original, digits) => {
    const months = Number(digits);
    return Number.isSafeInteger(months) && months >= 12 && months % 12 === 0 ? `${months / 12}년` : original;
  }).replace(/\s*~\s*/g, " ");
}

function basicRateText(value) {
  const period = easyPeriod(value.period);
  return value.period_basis === "contract_term"
    ? `${period} 가입하면 기본금리는 ${value.rate}예요.`
    : `기간 ${period}의 기본금리는 ${value.rate}예요.`;
}

function earlyRateText(value) {
  const minimum = value.minimum ? ` · ${value.minimum}` : "";
  return `${easyPeriod(value.period)}: ${value.rate}${minimum}`;
}

function addEvidence(parent, evidence, conditions = []) {
  if (!conditions.length && !evidence.length) return;
  const details = el("details", "evidence");
  details.append(el("summary", "evidence-title", "상세 조건·원문 근거 보기 · " + evidence.length + "개"));
  if (conditions.length) {
    const detailList = el("ul", "conditions");
    conditions.forEach(condition => detailList.append(el("li", "", condition)));
    details.append(detailList);
  }
  for (const ev of evidence) {
    const quote = el("div", "quote");
    quote.append(el("span", "document-label", ev.document_name + " · " + ev.document_id + " · " + ev.block_id));
    quote.append(el("p", "quote-text", ev.quote));
    details.append(quote);
  }
  parent.append(details);
}

function numberedReviewConditions(fact) {
  const numbered = [];
  for (const ev of fact.evidence) {
    const lines = ev.quote.split("\n").map(line => line.trim()).filter(Boolean);
    const start = lines.findIndex(line => /^[①②③④⑤]\s*\S/.test(line));
    if (start < 0) continue;
    const tail = lines.slice(start);
    if (!tail.every(line => /^[①②③④⑤]\s*\S/.test(line))) return [];
    numbered.push(...tail);
  }
  return numbered;
}

function priorityReviewContext(fact) {
  const context = [];
  for (const ev of fact.evidence) {
    const lines = ev.quote.split("\n").map(line => line.trim()).filter(Boolean);
    const numbered = lines.findIndex(line => /^[①②③④⑤]\s*\S/.test(line));
    if (numbered < 0) continue;
    const start = lines.findIndex((line, index) => index < numbered && /적용\s*조건\s*[:：]/.test(line) && /우대/.test(line));
    if (start >= 0) context.push(lines.slice(start, numbered).join(" "));
  }
  return context;
}

function renderFact(fact, options = {}) {
  const section = el("section", "fact");
  const header = el("div", "fact-heading");
  const stateClass = fact.status === "found" ? "found" : fact.status === "not_found_in_input" ? "missing" : "failed";
  header.append(el("span", "state " + stateClass, stateLabels[fact.status] || "추가 확인 필요"));
  const values = fact.status === "found" && Array.isArray(fact.structured_values) ? fact.structured_values : [];
  const isBasic = values.length && values.every(value => value.kind === "basic_rate");
  const isEarly = values.length && values.every(value => value.kind === "early_rate");
  if (isBasic) {
    header.append(el("p", "fact-text", basicRateText(values[0])));
  } else if (isEarly && options.earlyDetail) {
    header.append(el("p", "fact-text", "아래는 원문에서 확인한 기간별 조건이에요."));
  } else if (fact.status === "needs_review" && fact.statement.startsWith("원문 발췌: ")) {
    header.append(el("p", "fact-text", "적용 조건과 예외는 원문에서 확인해 주세요."));
  } else {
    header.append(el("p", "fact-text", fact.statement));
  }
  section.append(header);
  if (fact.important_conditions.length) {
    const conditionList = el("ul", "conditions");
    fact.important_conditions.forEach(condition => conditionList.append(el("li", "", condition)));
    section.append(conditionList);
  }
  // Only complete, numbered source lines are shown as review-only conditions.
  // The original evidence and paragraph IDs remain in the collapsed details.
  const reviewConditions = options.showReviewSource && fact.status === "needs_review" ? numberedReviewConditions(fact) : [];
  if (reviewConditions.length) {
    const source = el("div", "review-source");
    source.append(el("p", "context-note", "원문에 적힌 조건이에요. 적용 관계와 빠진 조건은 추가 확인해 주세요."));
    priorityReviewContext(fact).forEach(line => source.append(el("p", "context-note", line)));
    const conditions = el("ul", "conditions");
    reviewConditions.forEach(condition => conditions.append(el("li", "", condition)));
    source.append(conditions);
    section.append(source);
  }
  if (isBasic || isEarly) {
    const rows = el("ul", "rate-rows");
    values.forEach(value => rows.append(el("li", "", isBasic ? basicRateText(value) : earlyRateText(value))));
    if (isBasic) {
      const table = el("details", "evidence rate-details");
      table.append(el("summary", "evidence-title", `기간별 기본금리 전체 보기 · ${values.length}개`), rows);
      section.append(table);
    } else {
      section.append(rows);
    }
  }
  addEvidence(section, fact.evidence, fact.conditions);
  return section;
}

function renderHighlight(highlight) {
  const section = el("section", "highlight");
  section.append(el("span", "state found", "원문 근거 확인"));
  section.append(el("p", "highlight-text", highlight.statement));
  if (highlight.conditions?.length) {
    const conditions = el("ul", "conditions");
    highlight.conditions.forEach(condition => conditions.append(el("li", "", condition)));
    section.append(conditions);
  }
  section.append(el("p", "context-note", "원문 인용과 위치를 확인했어요. 다른 조건과 예외는 추가 확인해 주세요."));
  addEvidence(section, highlight.evidence);
  return section;
}

function render(categories, options = {}) {
  resultCards.replaceChildren();
  for (const item of categories) {
    const card = el("article", "card");
    card.append(el("h3", "", item.category));
    const basicFacts = item.category === "금리와 우대조건" && item.facts.some(f => f.status === "found" && f.structured_values?.some(v => v.kind === "basic_rate"));
    const earlyFacts = item.category === "중도해지 조건" ? item.facts.filter(f => f.status === "found" && f.structured_values?.length && f.structured_values.every(v => v.kind === "early_rate")) : [];
    const highlights = options.blocked ? [] : (item.display_highlights || []);
    if (options.showMissing && !options.blocked && item.category === "수수료" && !item.facts.some(f => f.status === "found")) {
      card.append(el("p", "summary", "입력한 상품설명서에서 일반 가입·해지 수수료에 대한 정보를 자동으로 확인하지 못했어요."));
      card.append(el("p", "fee-note", "수수료가 없거나 무료라는 뜻은 아니에요. 특정 조건에 따른 비용은 원문을 따로 확인해 주세요."));
    } else if (!basicFacts && !earlyFacts.length && !highlights.length) {
      card.append(el("p", "summary", item.summary));
    }
    if (!basicFacts) highlights.forEach(highlight => card.append(renderHighlight(highlight)));
    if (earlyFacts.length) {
      card.append(el("p", "context-note", "맡긴 기간에 따른 금리와 계산식은 아래에서 확인해 주세요."));
      card.append(el("p", "context-note", "기준일과 다른 적용 조건·예외도 원문에서 확인해 주세요."));
    }
    if (item.facts.length) {
      const list = el("div", "fact-list");
      let earlyTable = null;
      let highlightsInserted = false;
      let reviewDetails = null;
      const groupedReviews = item.facts.filter(fact => fact.status === "needs_review" && fact.evidence.length &&
        fact.important_conditions.every(condition => condition === "관련 조건과 예외가 모두 포함됐는지 추가 확인해 주세요.") &&
        !(item.category === "금리와 우대조건" && numberedReviewConditions(fact).length));
      const displayedBasic = new Set();
      for (const fact of item.facts) {
        if (fact.status === "found" && fact.structured_values?.length && fact.structured_values.every(v => v.kind === "basic_rate")) {
          // Only exact duplicates are hidden: differing values, conditions or
          // evidence remain independent results.
          const key = JSON.stringify(fact);
          if (displayedBasic.has(key)) continue;
          displayedBasic.add(key);
        }
        if (earlyFacts.includes(fact)) {
          if (!earlyTable) {
            earlyTable = el("details", "evidence rate-details");
            earlyTable.append(el("summary", "evidence-title", "기간별 중도해지 금리와 계산식 보기"));
            list.append(earlyTable);
          }
          earlyTable.append(renderFact(fact, {earlyDetail: true}));
        } else if (groupedReviews.includes(fact)) {
          if (!reviewDetails) {
            const section = el("section", "fact");
            const heading = el("div", "fact-heading");
            heading.append(el("span", "state failed", "추가 확인 필요"),
                           el("p", "fact-text", "일부 적용 조건과 예외는 원문을 다시 확인해 주세요."));
            reviewDetails = el("details", "evidence");
            reviewDetails.append(el("summary", "evidence-title", `추가 확인할 원문 보기 · ${groupedReviews.length}개`));
            section.append(heading, reviewDetails);
            list.append(section);
          }
          reviewDetails.append(renderFact(fact));
        } else {
          list.append(renderFact(fact, {showReviewSource: item.category === "금리와 우대조건"}));
          if (basicFacts && !highlightsInserted && fact.status === "found" && fact.structured_values?.some(v => v.kind === "basic_rate")) {
            highlights.forEach(highlight => list.append(renderHighlight(highlight)));
            highlightsInserted = true;
          }
        }
      }
      card.append(list);
    }
    if (item.unknowns.length) {
      card.append(el("p", "unknown-title", "추가 확인 사항"));
      const unknowns = el("ul", "unknowns");
      item.unknowns.forEach(value => unknowns.append(el("li", "", value)));
      card.append(unknowns);
    }
    resultCards.append(card);
  }
}

function resetResults() {
  render(categoryNames.map(category => ({category, summary: "분석 후 확인할 수 있습니다.", facts: [], unknowns: []})));
}

function renderDocuments(documents) {
  const details = document.getElementById("product-documents");
  const list = document.getElementById("product-document-list");
  list.replaceChildren();
  const scopes = {full: "전문", excerpt: "일부", unknown: "모름"};
  for (const document of documents) {
    const entry = el("div", "document-info-entry");
    entry.append(el("p", "document-label", document.name + " · " + document.id));
    const dateStatus = document.date_status === "validation_failed" ? " (근거 검증 실패)" : "";
    entry.append(el("p", "document-info-text", "입력 범위: " + scopes[document.scope] + " · 기준일·개정일: " + document.date + dateStatus));
    if (document.date_evidence.length) {
      const evidence = el("details", "evidence");
      evidence.append(el("summary", "evidence-title", "날짜 근거 보기 · " + document.date_evidence.length + "개"));
      for (const ev of document.date_evidence) {
        const quote = el("div", "quote");
        quote.append(el("span", "document-label", ev.document_name + " · " + ev.document_id + " · " + ev.block_id));
        quote.append(el("p", "quote-text", ev.quote));
        evidence.append(quote);
      }
      entry.append(evidence);
    }
    list.append(entry);
  }
  details.open = false;
  details.hidden = false;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  error.hidden = true;
  error.textContent = "";
  status.textContent = "";
  badge.textContent = "분석 전";
  alertBox.hidden = true;
  alertBox.textContent = "";
  document.getElementById("product-documents").hidden = true;
  document.getElementById("product-note").textContent = "입력 범위가 전문이어도 원본 전체를 검증한 것은 아닙니다. 금리와 조건은 반드시 원문과 대조해 주세요.";
  resetResults();
  const name = productName.value.trim();
  const documents = [...docs.children].map(item => ({
    name: item.querySelector(".document-name").value,
    product_name: item.querySelector(".document-product").value,
    scope: item.querySelector(".document-scope").value,
    date: item.querySelector(".document-date").value,
    text: item.querySelector(".document-text").value
  }));
  if (!name || documents.some(item => !item.name.trim() || !item.text.trim())) {
    error.textContent = "상품명과 각 문서의 이름, 본문을 입력해 주세요.";
    error.hidden = false;
    return;
  }
  if (documents.some(item => item.product_name.trim() !== name)) {
    error.textContent = "모든 문서의 상품명이 분석할 상품명과 같아야 합니다. 다른 상품은 따로 분석해 주세요.";
    error.hidden = false;
    return;
  }
  busy = true;
  submit.disabled = true;
  addDocument.disabled = true;
  submit.textContent = "분석 중…";
  status.textContent = "문서 전체를 한 번에 분석하고 원문 근거를 확인하고 있습니다.";
  badge.textContent = "분석 중";
  try {
    const response = await fetch("/api/products", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({product_name: name, documents})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "분석을 완료하지 못했습니다.");
    const state = result.processing_status;
    render(result.categories, {showMissing: true, blocked: state === "blocked"});
    renderDocuments(result.documents);
    badge.textContent = state === "complete" ? "처리 완료" : state === "partial" ? "부분 완료" : "분석 중단";
    const warnings = [];
    if (state === "partial") warnings.push("원문과 적용 조건을 추가 확인해 주세요. 검증되지 않은 AI 설명은 표시하지 않습니다.");
    if (result.product.suitability === "not_suitable") warnings.push("예·적금 상품 약관, 상품설명서 또는 금리표가 필요합니다.");
    if (result.product.consistency === "conflicting") warnings.push("서로 다른 상품으로 보이는 문서가 포함되어 결과를 표시하지 않습니다.");
    if (result.product.suitability === "uncertain" || result.product.consistency === "uncertain") warnings.push(state === "blocked" ? "문서가 같은 상품인지 확인할 수 없어 결과를 표시하지 않습니다." : "문서의 적합성이나 동일 상품 여부를 추가로 확인해 주세요.");
    if (warnings.length) {
      alertBox.textContent = warnings.join(" ");
      alertBox.hidden = false;
    }
    document.getElementById("product-note").textContent =
      "상품명: " + result.product.name + " · 입력 문서 " + result.documents.length +
      "개. 사용자 입력 범위만 분석하며 원본 전체와 동일 상품 여부를 보증하지 않습니다.";
    status.textContent = state === "complete" ? "처리가 완료됐습니다." : "확인이 필요한 결과가 있습니다.";
    inputToggle.hidden = false;
    expanded(false);
    document.getElementById("product-results-heading").focus({preventScroll: true});
  } catch (exception) {
    error.textContent = exception.message || "연결에 실패했습니다. 다시 시도해 주세요.";
    error.hidden = false;
    badge.textContent = "분석 실패";
    alertBox.textContent = "분석 실패: " + error.textContent;
    alertBox.hidden = false;
    expanded(true);
  } finally {
    busy = false;
    submit.disabled = false;
    submit.textContent = "상품 분석하기";
    status.textContent = badge.textContent === "분석 실패" ? "" : status.textContent;
    renumber();
  }
});

appendDocument();
resetResults();
