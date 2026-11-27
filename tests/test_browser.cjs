// Browser smoke test. Pass --live to make exactly one paid API call.
const {spawn} = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const chrome = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "terms-browser-"));
const child = spawn(chrome, [
  "--headless=new", "--disable-gpu", "--disable-background-networking",
  "--disable-logging", "--log-level=3", "--no-first-run", "--no-default-browser-check",
  "--incognito", "--remote-debugging-port=0", "--remote-allow-origins=*",
  "--user-data-dir=" + profile, "about:blank"
], {stdio: "ignore", windowsHide: true});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function get(url) {
  return new Promise((resolve, reject) => {
    http.get(url, r => {
      let content = "";
      r.on("data", chunk => content += chunk);
      r.on("end", () => resolve(JSON.parse(content)));
    }).on("error", reject);
  });
}
async function waitFor(fn, ms = 15000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const value = await fn();
    if (value) return value;
    await delay(200);
  }
  throw new Error("Timed out waiting for browser state.");
}
function assert(value, message) {
  if (!value) throw new Error(message);
}
async function main() {
  const portPath = path.join(profile, "DevToolsActivePort");
  await waitFor(() => fs.existsSync(portPath));
  const port = Number(fs.readFileSync(portPath, "utf8").split("\n")[0]);
  const tabs = await get("http://127.0.0.1:" + port + "/json/list");
  const socket = new WebSocket(tabs.find(tab => tab.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0;
  const pending = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const task = pending.get(message.id);
    pending.delete(message.id);
    message.error ? task.reject(new Error("Browser protocol error")) : task.resolve(message.result);
  };
  function send(method, params = {}) {
    const requestId = ++id;
    return new Promise((resolve, reject) => {
      pending.set(requestId, {resolve, reject});
      socket.send(JSON.stringify({id: requestId, method, params}));
    });
  }
  async function run(expression) {
    const result = await send("Runtime.evaluate", {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error("Browser script failed.");
    return result.result.value;
  }
  await send("Page.navigate", {url: process.env.TERMS_TEST_URL || "http://127.0.0.1:8000/"});
  await waitFor(() => run("document.readyState === 'complete' && !!document.querySelector('#analysis-form')"));
  await send("Emulation.setDeviceMetricsOverride", {width: 1280, height: 900, deviceScaleFactor: 1, mobile: false});
  assert(await run("document.querySelectorAll('.card').length === 6"), "Missing initial cards");
  await run("document.querySelector('#submit').click()");
  assert(await run("!document.querySelector('#error').hidden"), "Empty input was not rejected");

  // Check plain-text rendering and duplicate prevention without an API call.
  await run(`window.__realFetch = window.fetch; window.__fetchCount = 0;
    window.fetch = () => {window.__fetchCount++; return new Promise(resolve => window.__finishMock = resolve)};
    document.querySelector('#document').value = 'mock'; document.querySelector('#submit').click();
    document.querySelector('#submit').click();`);
  assert(await run("document.querySelector('#submit').disabled && window.__fetchCount === 1"), "Duplicate was not blocked");
  await run(`window.__finishMock({ok: true, json: async () => ({
    "항목": Array.from({length: 6}, (_, i) => ({"항목": "항목" + i, "확인 상태": i === 1 ? "근거 확인 실패" : i === 2 ? "입력에서 확인되지 않음" : "확인됨",
      "쉬운 설명": "<img src=x onerror=window.__injected=1>", "근거": [{"문단 번호":"P0001","근거 원문":"<svg onload=window.__injected=1>"}]})),
    "분석 상태": "완료", "입력 범위": "full"
  })})`);
  await waitFor(() => run("!document.querySelector('#submit').disabled"));
  assert(await run("!window.__injected && !document.querySelector('#cards img, #cards svg')"), "Unsafe HTML rendering");
  assert(await run("!!document.querySelector('.state.failed') && !!document.querySelector('.state.missing')"), "Failure and missing states are not distinct");
  await run(`window.fetch = async () => {throw new Error("mock failure")};
    document.querySelector('#submit').click();`);
  await waitFor(() => run("!document.querySelector('#submit').disabled"));
  assert(await run("!document.querySelector('#error').hidden && !document.querySelector('#result-alert').hidden && document.querySelector('#result-alert').textContent.includes('분석 실패') && !document.querySelector('#cards').textContent.includes('onerror')"), "Failure was hidden or stale result remains");

  // Replay the saved real sample response locally; fetch never contacts the API.
  const sample = fs.readFileSync(path.join(__dirname, "..", "sample.txt"), "utf8");
  const resultPath = path.join(__dirname, "..", "artifacts", "result-rights.json");
  const sampleBlocks = sample.replace(/\r\n?/g, "\n").split(/\n(?:[ \t]*\n)+/).filter(block => block.trim());
  const saved = fs.existsSync(resultPath)
    ? JSON.parse(fs.readFileSync(resultPath, "utf8"))
    : {"분석 상태": "완료", "항목": ["수집·이용", "보유·파기", "제3자 제공", "처리위탁", "국외 이전", "선택 동의·권리"].map((name, i) => ({
      "항목": name, "확인 상태": i < 3 ? "확인됨" : "입력에서 확인되지 않음",
      "쉬운 설명": i < 3 ? "조건과 예외를 포함한 설명입니다. ".repeat(12) : "입력에서 확인되지 않음",
      "근거": i < 3 ? [{"문단 번호": "P" + String(i + 1).padStart(4, "0"), "근거 원문": sampleBlocks[i]}] : []
    }))};
  saved["입력 범위"] = "full";
  await run("window.__initialResultWidth = document.querySelector('.results').getBoundingClientRect().width");
  await run(`window.__sampleResponse = ${JSON.stringify(saved)};
    window.__sampleText = ${JSON.stringify(sample)};
    window.fetch = async () => ({ok: true, json: async () => window.__sampleResponse});
    document.querySelector('#document').value = window.__sampleText;
    document.querySelector('#document').dispatchEvent(new Event('input'));
    document.querySelector('#scope').value = 'full';
    document.querySelector('#submit').click();`);
  await waitFor(() => run("!document.querySelector('#submit').disabled"));
  assert(await run("document.querySelector('#input-panel').hidden && document.querySelector('#workspace').classList.contains('result-focus')"), "Input did not collapse");
  assert(await run("document.querySelector('.results').getBoundingClientRect().width > window.__initialResultWidth * 1.3"), "Result area did not widen");
  assert(await run("document.querySelector('#input-toggle').textContent === '입력 문서 보기·수정'"), "Input reopen control missing");
  assert(await run("document.querySelectorAll('#cards .card').length === 6"), "Saved sample did not render six categories");
  assert(await run(`[...document.querySelectorAll('#cards .card')].every((card, i) => {
    const item = window.__sampleResponse['항목'][i];
    const text = card.querySelector('.summary');
    return (item['확인 상태'] === '입력에서 확인되지 않음' && item['쉬운 설명'] === item['확인 상태'])
      ? !text : !!text && text.textContent === item['쉬운 설명'];
  })`), "A summary was changed or truncated");
  assert(await run("[...document.querySelectorAll('#cards details')].every(d => !d.open)"), "Evidence is not initially collapsed");
  const expectedEvidence = saved["항목"].reduce((total, item) => total + item["근거"].length, 0);
  assert(await run(`[...document.querySelectorAll('#cards .card')].every((card, i) => {
    const items = window.__sampleResponse['항목'][i]['근거'];
    const d = card.querySelector('details');
    return !items.length ? !d : d && d.querySelector('summary').textContent === '근거 보기 · ' + items.length + '개';
  })`), "Evidence counts are incorrect");
  await run("document.querySelectorAll('#cards details').forEach(d => d.open = true)");
  assert(await run(`[...document.querySelectorAll('#cards .card')].every((card, i) => {
    const items = window.__sampleResponse['항목'][i]['근거'];
    const quotes = [...card.querySelectorAll('.quote')];
    return quotes.length === items.length && quotes.every((q, j) =>
      q.querySelector('.paragraph').textContent === items[j]['문단 번호'] &&
      q.querySelector('.quote-text').textContent === items[j]['근거 원문']);
  })`), "Evidence text or paragraph numbers changed");
  assert(await run(`document.querySelectorAll('#cards .quote').length === ${expectedEvidence}`), "Some evidence was not shown");
  await run("document.querySelector('#cards details summary').click()");
  assert(await run("!document.querySelector('#cards details').open"), "Evidence did not close");
  await run("document.querySelector('#cards details summary').click()");
  assert(await run("document.querySelector('#cards details').open"), "Evidence did not reopen");
  await run("document.querySelector('#input-toggle').click()");
  assert(await run("!document.querySelector('#input-panel').hidden && document.querySelector('#document').value === window.__sampleText"), "Input did not reopen intact");
  await run("document.querySelector('#input-toggle').click()");
  assert(await run("document.querySelector('#input-panel').hidden"), "Input did not collapse again");
  await send("Emulation.setDeviceMetricsOverride", {width: 320, height: 720, deviceScaleFactor: 1, mobile: true});
  assert(await run("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "Mobile page overflows horizontally");
  assert(await run(`[...document.querySelectorAll('#cards .summary, #cards .quote-text')].every(el =>
    el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1)`), "Mobile content is clipped");
  assert(await run("getComputedStyle(document.querySelector('#workspace')).gridTemplateColumns.split(' ').length === 1"), "Mobile layout is not one column");
  await run(`window.fetch = async () => ({ok: true, json: async () => ({
      ...window.__sampleResponse, '분석 상태': '부분 완료',
      '항목': window.__sampleResponse['항목'].map((item, i) => i < 3 ? {...item, '확인 상태': ['추가 확인 필요', '분석 실패', '부분 완료'][i]} : item)
    })});
    document.querySelector('#input-toggle').click(); document.querySelector('#submit').click();`);
  await waitFor(() => run("!document.querySelector('#submit').disabled"));
  assert(await run("!document.querySelector('#result-alert').hidden && document.querySelector('#result-alert').textContent.includes('부분 완료') && ['추가 확인 필요', '분석 실패', '부분 완료'].every(s => [...document.querySelectorAll('.state')].some(el => el.textContent === s && el.getClientRects().length))"), "Review warnings were hidden");
  await run(`window.fetch = async () => ({ok: true, json: async () => ({
      ...window.__sampleResponse, '분석 상태': '완료',
      '항목': window.__sampleResponse['항목'].map(item => ({...item, '확인 상태': '입력에서 확인되지 않음', '쉬운 설명': '입력에서 확인되지 않음', '근거': []}))
    })});
    document.querySelector('#input-toggle').click(); document.querySelector('#submit').click();`);
  await waitFor(() => run("!document.querySelector('#submit').disabled"));
  assert(await run("document.querySelector('#result-badge').textContent === '추가 확인 필요' && !document.querySelector('#result-alert').hidden && document.querySelectorAll('.state.missing').length === 6"), "All-missing result did not display a review warning");

  if (!process.argv.includes("--live")) {
    const origin = new URL(process.env.TERMS_TEST_URL || "http://127.0.0.1:8000/").origin;
    await send("Page.navigate", {url: origin + "/products"});
    await waitFor(() => run("document.readyState === 'complete' && !!document.querySelector('#product-form')"));
    await send("Emulation.setDeviceMetricsOverride", {width: 1280, height: 900, deviceScaleFactor: 1, mobile: false});
    assert(await run("document.querySelectorAll('#product-cards .card').length === 3"), "Product cards not shown");
    assert(await run("basicRateText({period:'12개월 이상 24개월 미만',rate:'연 2.80%',period_basis:'contract_term'}) === '1년 이상 2년 미만 가입하면 기본금리는 연 2.80%예요.' && basicRateText({period:'13개월 이상',rate:'연 1.50%'}).includes('기간 13개월 이상') && !basicRateText({period:'13개월 이상',rate:'연 1.50%'}).includes('가입하면')"),
      "Duration conversion or unverified contract-term fallback failed");
    assert(await run("numberedReviewConditions({evidence:[{quote:'① 조건 하나\\n② 조건 둘'}]}).length === 2 && numberedReviewConditions({evidence:[{quote:'① 긴 조건\\n이어지는 예외'}]}).length === 0"),
      "Multiline source condition was shortened or dropped");
    assert(await run(`(() => {
      const ev={document_name:'가상',document_id:'D001',block_id:'P0001',quote:'표'};
      const fact={status:'found',statement:'표',important_conditions:['조건 A'],conditions:[],evidence:[ev],
        structured_values:[{kind:'basic_rate',period:'6개월 이상',rate:'연 2%'}]};
      const changed={...fact,important_conditions:['조건 B']};
      render([{category:'금리와 우대조건',summary:'',facts:[fact,structuredClone(fact),changed],unknowns:[]}]);
      const rows=[...document.querySelectorAll('#product-cards .fact')];
      const ok=rows.length===2 && rows[0].textContent.includes('조건 A') && rows[1].textContent.includes('조건 B');
      resetResults(); return ok;
    })()`), "Basic-rate deduplication removed different conditions or kept an exact duplicate");
    assert(await run(`(() => {const f={evidence:[{quote:'우대이율\\n적용조건 : 아래 조건이면 최대 연 0.5%p 우대\\n단, 동의를 만기까지 유지\\n① 첫째'}]};
      return priorityReviewContext(f)[0].includes('최대 연 0.5%p') && priorityReviewContext(f)[0].includes('동의를 만기까지 유지');})()`),
      "Priority source context lost its cap or condition");
    assert(await run(`(() => {const f=renderFact({status:'needs_review',statement:'원문 발췌: 예시',important_conditions:[],conditions:[],
      evidence:[{document_name:'가상',document_id:'D001',block_id:'P0001',quote:'① 긴 조건\\n이어지는 예외'}]}, {showReviewSource:true});
      return !f.querySelector('.review-source') && !f.querySelector('details').open && f.querySelector('.quote-text').textContent === '① 긴 조건\\n이어지는 예외';})()`),
      "Multiline condition was lost rather than kept in collapsed evidence");
    const fictional = fs.readFileSync(path.join(__dirname, "fixtures", "fictional-deposit.txt"), "utf8");
    await run(`window.__productFetchCount = 0;
      window.fetch = (url, opts) => {window.__productFetchCount++; window.__productRequest = JSON.parse(opts.body);
        return new Promise(resolve => window.__finishProduct = resolve)};
      document.querySelector('#product-name').value = '테스트 정기예금';
      document.querySelector('#product-name').dispatchEvent(new Event('input'));
      document.querySelector('.document-name').value = '가상 상품설명서';
      document.querySelector('.document-product').value = '테스트 정기예금';
      document.querySelector('.document-text').value = ${JSON.stringify(fictional)};
      document.querySelector('#add-document').click();
      document.querySelectorAll('.document-name')[1].value = '가상 금리표';
      document.querySelectorAll('.document-product')[1].value = '테스트 정기예금';
      document.querySelectorAll('.document-text')[1].value = '가상 금리표 본문';
      document.querySelector('#product-submit').click(); document.querySelector('#product-submit').click();`);
    assert(await run("document.querySelector('#product-submit').disabled && window.__productFetchCount === 1 && window.__productRequest.documents.length === 2"), "Product duplicate prevention or documents failed");
    await run(`window.__finishProduct({ok: true, json: async () => ({
      processing_status:'partial',
      product:{name:'테스트 정기예금',suitability:'suitable',consistency:'consistent',note:''},
      documents:[{id:'D001',name:'가상 상품설명서',scope:'full',date:'미확인',date_status:'not_found_in_input',date_evidence:[]},
        {id:'D002',name:'가상 금리표',scope:'unknown',date:'미확인',date_status:'not_found_in_input',date_evidence:[]}],
      categories:[
        {category:'금리와 우대조건',summary:'기간과 조건을 확인하세요.',
         facts:[{statement:'<img src=x onerror=window.__productInjected=1>',status:'found',
           important_conditions:['월 50만원 이상 3회, 6개월 유지','중도해지 시 제외'],conditions:['상세 확인 조건'],
           evidence:[{document_name:'가상 상품설명서',document_id:'D001',block_id:'P0001',quote:'<svg onload=window.__productInjected=1>'}]},
          {statement:'원문 발췌: 테스트',status:'needs_review',important_conditions:[],conditions:[],
           evidence:[{document_name:'가상 상품설명서',document_id:'D001',block_id:'P0002',quote:'① <img src=x onerror=window.__productInjected=1>'}]}],
         unknowns:['기준일 추가 확인']},
        {category:'수수료',summary:'금액은 미확인입니다.',
         facts:[{statement:'근거 검증에 실패해 이 사실을 표시하지 않습니다.',status:'validation_failed',important_conditions:[],conditions:[],evidence:[]}],
         unknowns:['별도 수수료표 확인']},
        {category:'중도해지 조건',summary:'입력에서 확인된 사실이 없습니다.',
         facts:[{statement:'입력에서 관련 내용을 확인하지 못했습니다.',status:'not_found_in_input',important_conditions:[],conditions:[],evidence:[]}],unknowns:[]}
      ]
    })})`);
    await waitFor(() => run("!document.querySelector('#product-submit').disabled"));
    assert(await run("document.querySelector('#product-input').hidden && document.querySelector('#product-workspace').classList.contains('result-focus')"), "Product input did not collapse");
    assert(await run("document.querySelectorAll('#product-cards .card').length === 3 && !document.querySelector('#product-alert').hidden"), "Product results or partial warning absent");
    assert(await run("!window.__productInjected && !document.querySelector('#product-cards img, #product-cards svg')"), "Product result executed HTML");
    assert(await run("document.querySelector('.review-source')?.textContent.includes('① <img') && document.querySelector('.review-source').closest('.fact').querySelector('.state').textContent === '추가 확인 필요'"), "Review source was hidden, executed, or shown as confirmed");
    assert(await run("document.querySelector('.conditions').textContent.includes('50만원') && document.querySelector('.conditions').textContent.includes('중도해지 시 제외')"), "Product conditions not visible");
    assert(await run("document.querySelector('#product-cards details') && !document.querySelector('#product-cards details').open"), "Product evidence not collapsed");
    await run("document.querySelector('#product-cards details summary').click()");
    assert(await run("document.querySelector('#product-cards details').open && document.querySelector('#product-cards .quote .document-label').textContent.includes('P0001')"), "Product evidence did not open");
    await run("document.querySelector('#product-input-toggle').click()");
    assert(await run("!document.querySelector('#product-input').hidden && document.querySelectorAll('.document-entry').length === 2"), "Product input did not reopen");
    if (process.env.TERMS_REPLAY_FILE) {
      const replay = JSON.parse(fs.readFileSync(process.env.TERMS_REPLAY_FILE, "utf8"));
      const rate = replay.categories[0].facts.find(f => f.structured_values?.some(v => v.kind === "basic_rate"));
      const floor = replay.categories[2].facts.find(f => f.structured_values?.some(v => v.minimum));
      assert(rate && floor, "Replay has no structured rates");
      await run(`window.fetch = async () => ({ok:true, json:async () => (${JSON.stringify(replay)})});
        document.querySelector('#product-submit').click()`);
      await waitFor(() => run("!document.querySelector('#product-submit').disabled"));
      assert(await run("[...document.querySelectorAll('#product-cards details')].every(d => !d.open) && [...document.querySelectorAll('#product-cards .quote')].every(q => q.closest('details:not([open])'))"),
        "Original evidence or details were not initially collapsed");
      assert(await run("[...document.querySelectorAll('#product-cards .state.found')].every(e => e.textContent === '원문 근거 확인')"),
        "Evidence status implies a broader verification");
      assert(await run("[...document.querySelectorAll('#product-cards .fact')].some(f => f.querySelector('.state')?.textContent === '추가 확인 필요' && f.querySelector('.review-source')?.textContent.includes('①') && f.querySelector('.review-source')?.textContent.includes('③') && f.querySelector('.review-source').getClientRects().length)"),
        "Review-only priority conditions are missing or presented as confirmed");
      assert(await run("[...document.querySelectorAll('#product-cards .card')][2].textContent.includes('추가 확인할 원문 보기 · 4개') && [...document.querySelectorAll('#product-cards .card')][2].querySelector('.state.failed').getClientRects().length"),
        "Grouped review state or source access is missing");
      assert(await run("['P0022','P0037','P0049','P0051'].every(id => [...document.querySelectorAll('#product-cards .card')][2].textContent.includes(id))"),
        "Grouping dropped source references");
      if (process.env.TERMS_CAPTURE_VISIBLE === "1") {
        const visible = await run(`({badge: document.querySelector('#product-badge').innerText,
          alert: document.querySelector('#product-alert').innerText,
          note: document.querySelector('#product-note').innerText,
          cards: [...document.querySelectorAll('#product-cards .card')].map(card => card.innerText),
          disclaimer: document.querySelector('.results .disclaimer').innerText})`);
        console.log("VISIBLE_RESULT " + JSON.stringify(visible));
      }
      assert(await run("[...document.querySelectorAll('#product-cards .fact-text')].some(e => e.textContent.includes('6개월 이상 1년 미만 가입하면 기본금리는 연 2.60%예요.') && e.getClientRects().length)"),
        "Easy basic rate missing from the main view");
      assert(await run("[...document.querySelectorAll('#product-cards details')].some(d => d.querySelector('summary')?.textContent.includes('기간별 기본금리 전체 보기') && !d.open && d.textContent.includes('1년 이상 2년 미만 가입하면 기본금리는 연 2.80%예요.'))"),
        "Complete basic-rate table missing or initially open");
      await run("[...document.querySelectorAll('#product-cards details > summary')].find(s => s.textContent.includes('기간별 기본금리 전체 보기')).click()");
      assert(await run("[...document.querySelectorAll('#product-cards details')].some(d => d.open && d.querySelector('summary')?.textContent.includes('기간별 기본금리 전체 보기') && [...d.querySelectorAll('li')].some(li => li.textContent.includes('1년 이상 2년 미만') && li.getClientRects().length))"),
        "Basic-rate details did not open");
      assert(await run("[...document.querySelectorAll('#product-cards .highlight-text')].some(e => e.textContent.includes('만기 전에 해지하면 약속한 금리보다 낮은 금리가 적용돼요.') && e.getClientRects().length)"),
        "Source-backed early termination explanation missing");
      assert(await run("[...document.querySelectorAll('#product-cards .highlight-text')].some(e => e.textContent.includes('우대금리가 적용되지 않아요.') && e.getClientRects().length)"),
        "Priority-rate exclusion missing");
      assert(await run("[...document.querySelectorAll('#product-cards details')].some(d => d.querySelector('summary')?.textContent.includes('기간별 중도해지 금리와 계산식 보기') && !d.open && d.textContent.includes('3개월 이상 6개월 미만: 기본이율×50%×보유일수/계약일수 · 최저이율 연 0.15%'))"),
        "Early-rate minimum missing its period or detail is initially open");
      await run("[...document.querySelectorAll('#product-cards details > summary')].find(s => s.textContent.includes('기간별 중도해지 금리와 계산식 보기')).click()");
      assert(await run("[...document.querySelectorAll('#product-cards .rate-rows li')].some(li => li.textContent.includes('3개월 이상 6개월 미만') && li.textContent.includes('최저이율 연 0.15%') && li.getClientRects().length) && [...document.querySelectorAll('#product-cards .rate-rows li')].some(li => li.textContent.includes('3개월 미만: 연 0.1%') && !li.textContent.includes('0.15%'))"),
        "Early-rate details do not preserve the minimum's period");
      if (process.env.TERMS_CAPTURE_VISIBLE === "1") {
        const detailRows = await run(`(() => {
          const details = [...document.querySelectorAll('#product-cards details')];
          const basic = details.find(d => d.querySelector('summary')?.textContent.includes('기간별 기본금리 전체 보기'));
          const early = details.find(d => d.querySelector('summary')?.textContent.includes('기간별 중도해지 금리와 계산식 보기'));
          return {basic: [...basic.querySelectorAll('.rate-rows li')].map(li => li.innerText),
                  early: [...early.querySelectorAll('.rate-rows li')].map(li => li.innerText)};
        })()`);
        console.log("VISIBLE_DETAILS " + JSON.stringify(detailRows));
      }
      assert(await run("document.querySelectorAll('#product-cards .card')[1].textContent.includes('일반 가입·해지 수수료에 대한 정보를 자동으로 확인하지 못했어요.') && !document.querySelectorAll('#product-cards .card')[1].textContent.includes('0원')"),
        "Missing fee was misrepresented");
      assert(await run("document.querySelectorAll('#product-cards .card')[1].textContent.includes('위법계약 해지에는 정해진 조건에 따른 비용 제한이 있어요.') && document.querySelectorAll('#product-cards .card')[1].textContent.includes('정당한 사유 없이') && document.querySelectorAll('#product-cards .card')[1].textContent.includes('계약일부터 5년 이내')"),
        "Conditional fee restriction and its conditions were not displayed");
      assert(await run("document.querySelector('#product-cards').textContent.includes('추가 확인 필요')"),
        "Review status missing");
      const expectedFailure = replay.categories.some(c => c.facts.some(f => f.status === 'validation_failed'));
      assert((await run("document.querySelector('#product-cards').textContent.includes('근거 검증 실패')")) === expectedFailure,
        "Evidence failure status does not match the replayed result");
      assert(await run("!document.querySelector('#product-cards').textContent.includes('중도해지 시점의 영업점')"),
        "Unverified conflicting condition was rendered");
      console.log("Saved response rendered with simple rates, source-backed conditions, and collapsed details.");
      await run("document.querySelector('#product-input-toggle').click()");
    }
    await send("Emulation.setDeviceMetricsOverride", {width: 320, height: 720, deviceScaleFactor: 1, mobile: true});
    assert(await run("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "Product page overflows on mobile");
    await run(`window.fetch = async () => ({ok:false, json:async () => ({error:'테스트 오류'})}); document.querySelector('#product-submit').click()`);
    await waitFor(() => run("!document.querySelector('#product-submit').disabled"));
    assert(await run("!document.querySelector('#product-error').hidden && document.querySelector('#product-badge').textContent === '분석 실패' && !document.querySelector('#product-cards').textContent.includes('50만원')"), "Product error retained old result");
    console.log("Privacy and product browser checks passed; no API call was made.");
    socket.close();
    return;
  }
  await run("document.querySelector('#input-toggle').click()");
  await run(`window.__http = 0; window.__realCount = 0;
    window.fetch = (...args) => {window.__realCount++; return window.__realFetch(...args).then(r => {window.__http = r.status; return r})};
    document.querySelector('#document').value = ${JSON.stringify(sample)};
    document.querySelector('#document').dispatchEvent(new Event('input'));
    document.querySelector('#scope').value = 'full';
    document.querySelector('#submit').click();`);
  assert(await run("document.querySelector('#submit').disabled"), "Loading state was not shown");
  await waitFor(() => run("!document.querySelector('#submit').disabled"), 110000);
  const summary = await run(`(() => {
    const cards = [...document.querySelectorAll('#cards .card')];
    const blocks = document.querySelector('#document').value.split(/\\n(?:[ \\t]*\\n)+/).filter(x => x.trim());
    const quotes = [...document.querySelectorAll('#cards .quote')];
    return {http: window.__http, calls: window.__realCount, cards: cards.length,
      evidence: quotes.length, evidenceMatches: quotes.every(q => {
        const number = Number(q.querySelector('.paragraph').textContent.slice(1));
        return blocks[number - 1]?.includes(q.querySelector('.quote-text').textContent);
      }), missing: cards.filter(c => c.querySelector('.state').textContent === '입력에서 확인되지 않음').length,
      failed: cards.filter(c => c.querySelector('.state').textContent === '근거 확인 실패').length,
      notePresent: document.querySelector('#result-note').textContent.includes('검증하지 않습니다'),
      error: !document.querySelector('#error').hidden};
  })()`);
  console.log(JSON.stringify(summary));
  assert(summary.http === 200 && summary.calls === 1 && summary.cards === 6 && summary.evidence > 0
    && summary.evidenceMatches && summary.notePresent && !summary.error, "Real browser analysis failed");
  socket.close();
}
main().catch(error => { console.error("Browser verification failed:", error.message || "browser connection error"); process.exitCode = 1; })
  .finally(() => child.kill());
