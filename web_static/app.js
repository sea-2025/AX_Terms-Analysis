"use strict";

const categories = ["수집·이용", "보유·파기", "제3자 제공", "처리위탁", "국외 이전", "선택 동의·권리"];
const form = document.getElementById("analysis-form");
const input = document.getElementById("document");
const scope = document.getElementById("scope");
const cards = document.getElementById("cards");
const button = document.getElementById("submit");
const status = document.getElementById("status");
const error = document.getElementById("error");
const badge = document.getElementById("result-badge");
const inputPanel = document.getElementById("input-panel");
const inputToggle = document.getElementById("input-toggle");
const workspace = document.getElementById("workspace");
const resultAlert = document.getElementById("result-alert");
let busy = false;

function setInputExpanded(expanded) {
  inputPanel.hidden = !expanded;
  inputToggle.setAttribute("aria-expanded", String(expanded));
  inputToggle.textContent = expanded ? "입력 문서 접기" : "입력 문서 보기·수정";
  workspace.classList.toggle("result-focus", !expanded);
}

inputToggle.addEventListener("click", () => {
  setInputExpanded(inputPanel.hidden);
  if (!inputPanel.hidden) input.focus();
});

function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = value;
  return element;
}

function render(items) {
  cards.replaceChildren();
  for (const item of items) {
    const card = node("article", "card");
    const heading = node("div", "card-heading");
    heading.append(node("h3", "", item["항목"]));
    const state = item["확인 상태"];
    const stateClass = state === "확인됨" ? "found" : ["근거 확인 실패", "추가 확인 필요", "분석 실패", "부분 완료"].includes(state) ? "failed" : state === "입력에서 확인되지 않음" ? "missing" : "waiting";
    heading.append(node("span", "state " + stateClass, state));
    card.append(heading);
    if (state !== "입력에서 확인되지 않음" || item["쉬운 설명"] !== state) {
      card.append(node("p", "summary", item["쉬운 설명"]));
    }
    if (item["근거"].length) {
      const evidence = node("details", "evidence");
      evidence.append(node("summary", "evidence-title", "근거 보기 · " + item["근거"].length + "개"));
      for (const entry of item["근거"]) {
        const block = node("div", "quote");
        block.append(node("span", "paragraph", entry["문단 번호"]));
        block.append(node("p", "quote-text", entry["근거 원문"]));
        evidence.append(block);
      }
      card.append(evidence);
    }
    cards.append(card);
  }
}

function emptyResults(message = "분석 후 확인할 수 있습니다.") {
  render(categories.map(category => ({"항목": category, "확인 상태": "분석 전", "쉬운 설명": message, "근거": []})));
}

input.addEventListener("input", () => {
  document.getElementById("character-count").textContent = input.value.length.toLocaleString("ko-KR") + "자";
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  error.hidden = true;
  error.textContent = "";
  status.textContent = "";
  badge.textContent = "분석 전";
  resultAlert.hidden = true;
  resultAlert.textContent = "";
  document.getElementById("result-note").textContent = "결과는 입력된 내용에 한정됩니다. 입력 범위를 전문으로 선택해도 원본 전체 여부는 검증하지 않습니다.";
  emptyResults();
  if (!input.value.trim()) {
    error.textContent = "공개 문서 내용을 입력해 주세요.";
    error.hidden = false;
    input.focus();
    return;
  }
  busy = true;
  button.disabled = true;
  button.textContent = "분석 중…";
  status.textContent = "문서를 분석하고 근거를 확인하고 있습니다. 잠시 기다려 주세요.";
  badge.textContent = "분석 중";
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text: input.value, scope: scope.value})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "분석을 완료하지 못했습니다.");
    render(result["항목"]);
    const scopeLabels = {full: "전문", excerpt: "일부", unknown: "모름"};
    badge.textContent = result["분석 상태"];
    document.getElementById("result-note").textContent =
      "입력 범위: " + scopeLabels[result["입력 범위"]] + ". 결과는 입력된 내용에 한정되며, 원본 전체 여부는 검증하지 않습니다.";
    status.textContent = result["분석 상태"] === "완료" ? "분석이 완료됐습니다." : "분석이 끝났지만 일부 근거를 확인하지 못했습니다.";
    const allMissing = result["항목"].length === categories.length &&
      result["항목"].every(item => item["확인 상태"] === "입력에서 확인되지 않음");
    if (allMissing) {
      badge.textContent = "추가 확인 필요";
      resultAlert.textContent = "추가 확인 필요: 입력에서 여섯 항목을 모두 찾지 못했습니다. 문서 본문이 입력되었는지 확인해 주세요. 분석 과정에서 내용을 놓쳤을 가능성도 있습니다.";
      resultAlert.hidden = false;
    } else if (result["분석 상태"] !== "완료") {
      resultAlert.textContent = result["분석 상태"] + ": 일부 결과를 확인하지 못했습니다. 항목별 상태를 확인해 주세요.";
      resultAlert.hidden = false;
    }
    inputToggle.hidden = false;
    setInputExpanded(false);
    document.getElementById("results-heading").focus({preventScroll: true});
  } catch (err) {
    error.textContent = err.message || "연결에 실패했습니다. 다시 시도해 주세요.";
    error.hidden = false;
    status.textContent = "";
    badge.textContent = "분석 실패";
    resultAlert.textContent = "분석 실패: " + (err.message || "연결에 실패했습니다. 다시 시도해 주세요.");
    resultAlert.hidden = false;
    setInputExpanded(true);
  } finally {
    busy = false;
    button.disabled = false;
    button.textContent = "분석하기";
  }
});

emptyResults();
