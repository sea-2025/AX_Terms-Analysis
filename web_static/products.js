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
  found: "원문 확인",
  not_found_in_input: "입력에서 확인되지 않음",
  needs_review: "추가 확인 필요",
  validation_failed: "근거 검증 실패"
};

function render(categories) {
  resultCards.replaceChildren();
  for (const item of categories) {
    const card = el("article", "card");
    card.append(el("h3", "", item.category));
    card.append(el("p", "summary", item.summary));
    if (item.facts.length) {
      const list = el("div", "fact-list");
      for (const fact of item.facts) {
        const section = el("section", "fact");
        const header = el("div", "fact-heading");
        const stateClass = fact.status === "found" ? "found" : fact.status === "not_found_in_input" ? "missing" : "failed";
        header.append(el("span", "state " + stateClass, stateLabels[fact.status] || "추가 확인 필요"));
        header.append(el("p", "fact-text", fact.statement));
        section.append(header);
        if (fact.important_conditions.length) {
          const conditionList = el("ul", "conditions");
          fact.important_conditions.forEach(condition => conditionList.append(el("li", "", condition)));
          section.append(conditionList);
        }
        if (fact.conditions.length || fact.evidence.length) {
          const details = el("details", "evidence");
          details.append(el("summary", "evidence-title", "상세 조건·원문 근거 보기 · " + fact.evidence.length + "개"));
          if (fact.conditions.length) {
            const detailList = el("ul", "conditions");
            fact.conditions.forEach(condition => detailList.append(el("li", "", condition)));
            details.append(detailList);
          }
          for (const ev of fact.evidence) {
            const quote = el("div", "quote");
            quote.append(el("span", "document-label", ev.document_name + " · " + ev.document_id + " · " + ev.block_id));
            quote.append(el("p", "quote-text", ev.quote));
            details.append(quote);
          }
          section.append(details);
        }
        list.append(section);
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
    render(result.categories);
    renderDocuments(result.documents);
    const state = result.processing_status;
    badge.textContent = state === "complete" ? "처리 완료" : state === "partial" ? "부분 완료" : "분석 중단";
    const warnings = [];
    if (state === "partial") warnings.push("일부 근거를 검증하지 못했습니다. 해당 사실과 핵심 설명은 숨겼습니다.");
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
