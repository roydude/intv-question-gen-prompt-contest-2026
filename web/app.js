/* 잡다 프롬프트 콘테스트 로컬 화면 */
const MONACO = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min';
const S = {
  state: null, prompts: null, cases: [], stage: 'assignments', saved: {}, current: {},
  side: true, diff: null, models: {}, fallback: false, decos: [], result: null,
  genScope: 'all', genPick: new Set(), genChange: null, genEst: null,
  poll: null, updTimer: null, chgTimer: null, change: null, lastSavedAt: null,
};
const SN = { expanded: false, stay: false, timer: null, est: null, doneRunId: null, mode: null, res: null };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---------------- 공통 ---------------- */
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new Error((j && (j.error || j.message)) || `요청이 실패했어요 (${r.status})`);
  return j;
}
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add('hidden'), 2600);
}
function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso); const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function hm(iso) { const d = new Date(iso); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; }
function pct(ratio) { return ratio >= 1 ? '100% 이상' : `${(ratio * 100).toFixed(1)}%`; }
function confirmBox(title, text, yes = '확인') {
  return new Promise((res) => {
    $('cfTitle').textContent = title; $('cfText').textContent = text; $('cfYes').textContent = yes;
    openModal('confirmModal');
    const done = (v) => { closeModal('confirmModal'); $('cfYes').onclick = $('cfNo').onclick = null; res(v); };
    $('cfYes').onclick = () => done(true); $('cfNo').onclick = () => done(false);
  });
}
function openModal(id) { $(id).classList.remove('hidden'); }
function closeModal(id) { $(id).classList.add('hidden'); }
const chainById = (id) => S.state.chain.find((c) => c.id === id);
const isRunning = () => !!(S.state && S.state.running && S.state.running.status === 'running');
const isDirty = (id) => (id ? S.current[id] !== S.saved[id] : Object.keys(S.saved).some((k) => S.current[k] !== S.saved[k]));
const failName = (code) => (S.state.failTypes.find((f) => f.id === code) || {}).name || code;
const ruleName = (id) => (S.state.rules.find((r) => r[0] === id) || [id, id])[1];

/* 패널 열림 상태는 이 PC 브라우저에만 기억한다 */
function loadUI() {
  const def = { panel: false, open: { result: true, cases: false, remain: false } };
  try { const v = JSON.parse(localStorage.getItem('contest.ui') || '{}'); return { ...def, ...v, open: { ...def.open, ...(v.open || {}) } }; } catch { return def; }
}
const UI = loadUI();
function saveUI() { try { localStorage.setItem('contest.ui', JSON.stringify(UI)); } catch { /* 저장 못 해도 화면은 그대로 */ } }

/* ---------------- 형식 영역·줄 비교 ---------------- */
function formatInfo(text) {
  const lines = text.split('\n'); const sections = {}; const ranges = []; let cur = null; let head = 0;
  lines.forEach((line, i) => {
    const s = line.trim();
    if (s.startsWith('#')) {
      const level = s.length - s.replace(/^#+/, '').length; const title = s.replace(/^#+/, '').trim(); const low = title.toLowerCase();
      if (level <= 3) {
        if (cur !== null) ranges.push([head + 1, i]);
        if (['input', 'output', 'format'].some((w) => low.includes(w)) && !low.includes('example')) { cur = title; sections[cur] = []; head = i; return; }
        cur = null; return;
      }
    }
    if (cur !== null) sections[cur].push(line);
  });
  if (cur !== null) ranges.push([head + 1, lines.length]);
  Object.keys(sections).forEach((k) => { sections[k] = sections[k].join('\n').trim(); });
  return { sections, ranges };
}
function formatChanged(id, text) {
  const base = formatInfo(S.prompts[id].base).sections; const mine = formatInfo(text).sections;
  return Object.keys(base).filter((k) => mine[k] !== base[k]);
}
function diffCounts(a, b) {
  const A = a.split('\n'); const B = b.split('\n'); let s = 0;
  while (s < A.length && s < B.length && A[s] === B[s]) s++;
  let ea = A.length; let eb = B.length;
  while (ea > s && eb > s && A[ea - 1] === B[eb - 1]) { ea--; eb--; }
  const x = A.slice(s, ea); const y = B.slice(s, eb); const n = x.length; const m = y.length;
  if (n * m > 4e6) return { add: m, rem: n };
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  return { add: m - dp[0][0], rem: n - dp[0][0] };
}

/* ---------------- 편집기 ---------------- */
function setupEditor() {
  const giveUp = setTimeout(useFallback, 9000);
  if (!window.require) { clearTimeout(giveUp); useFallback(); return; }
  window.MonacoEnvironment = {
    getWorkerUrl: () => `data:text/javascript;charset=utf-8,${encodeURIComponent(`self.MonacoEnvironment={baseUrl:'${MONACO}/'};importScripts('${MONACO}/vs/base/worker/workerMain.js');`)}`,
  };
  window.require.config({ paths: { vs: `${MONACO}/vs` } });
  window.require(['vs/editor/editor.main'], () => {
    clearTimeout(giveUp);
    if (S.fallback) return;
    for (const c of S.state.chain) {
      const orig = monaco.editor.createModel(S.prompts[c.id].base, 'markdown');
      const mod = monaco.editor.createModel(S.current[c.id], 'markdown');
      mod.onDidChangeContent(() => { S.current[c.id] = mod.getValue(); scheduleUpdate(); });
      S.models[c.id] = { orig, mod };
    }
    S.diff = monaco.editor.createDiffEditor($('editor'), {
      renderSideBySide: S.side, originalEditable: false, readOnly: false, automaticLayout: true, wordWrap: 'on', diffWordWrap: 'on',
      minimap: { enabled: false }, fontSize: 13, lineHeight: 21, renderOverviewRuler: false, scrollBeyondLastLine: false,
      ignoreTrimWhitespace: false, glyphMargin: true, fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
      renderGutterMenu: false, renderMarginRevertIcon: false, useInlineViewWhenSpaceIsLimited: false,
    });
    setStageModel();
  }, () => { clearTimeout(giveUp); useFallback(); });
}
function useFallback() {
  if (S.diff || S.fallback) return;
  S.fallback = true;
  $('editor').classList.add('hidden'); $('fallbackEditor').classList.remove('hidden'); $('edCols').classList.add('single');
  $('viewSeg').classList.add('hidden');
  const ta = $('fallbackEditor');
  ta.value = S.current[S.stage];
  ta.oninput = () => { S.current[S.stage] = ta.value; scheduleUpdate(); };
  showBanner('편집기를 불러오지 못해 기본 입력창으로 열었어요. 인터넷 연결을 확인하면 나란히 비교 화면을 쓸 수 있어요.', 'org', 'monaco');
  updateEditorMeta();
}
function setStageModel() {
  if (S.fallback) { $('fallbackEditor').value = S.current[S.stage]; }
  else if (S.diff) { S.diff.setModel({ original: S.models[S.stage].orig, modified: S.models[S.stage].mod }); }
  $('edFile').textContent = `prompts/${chainById(S.stage).file}`;
  updateEditorMeta();
}
function scheduleUpdate() { clearTimeout(S.updTimer); S.updTimer = setTimeout(updateEditorMeta, 250); }
function updateEditorMeta() {
  const id = S.stage; const base = S.prompts[id].base; const mine = S.current[id];
  const lines = (t) => t.replace(/\n$/, '').split('\n').length;
  $('edLines').textContent = `${lines(base)}줄 → ${lines(mine)}줄`;
  const d = diffCounts(base, mine);
  $('edDiff').textContent = d.add || d.rem ? `v9.0.0 대비 +${d.add}줄 −${d.rem}줄` : 'v9.0.0과 같아요';
  const fc = formatChanged(id, mine);
  const fs = $('fmtStatus');
  fs.textContent = fc.length ? `⚠ 형식 영역이 바뀌었어요: ${fc.join(', ')}` : '✓ 입력·출력 형식 영역 변경 없음';
  fs.className = `b6 ${fc.length ? 'org' : 'green'}`;
  if (S.diff && window.monaco) {
    const ed = S.diff.getModifiedEditor();
    S.decos = ed.deltaDecorations(S.decos, formatInfo(mine).ranges.map(([a, b]) => ({
      range: new monaco.Range(a, 1, Math.max(a, b), 1),
      options: { isWholeLine: true, className: 'fmt-zone', glyphMarginClassName: 'fmt-glyph', hoverMessage: { value: '형식 영역 · 키 이름과 구조를 바꾸면 생성이 실패해요' } },
    })));
  }
  renderDirty();
  scheduleChange();
}
/* ③의 v9.0.0 대비 바뀐 분량은 서버가 계산한다. 생성과 제출이 같은 기준(3%)을 쓴다 */
function scheduleChange() {
  clearTimeout(S.chgTimer);
  S.chgTimer = setTimeout(async () => {
    try { S.change = await api('/api/prompts/change', { id: 'assignments', text: S.current.assignments }); } catch { return; }
    renderChange(); renderTop();
  }, 500);
}
function blockMsg(c) {
  const need = `${Math.round(c.min * 100)}%`;
  if (c.identical) return `③이 v9.0.0과 같아서 질문을 생성할 수 없어요. ${need} 이상 바꿔 주세요.`;
  return `③ 바뀐 분량이 ${(c.ratio * 100).toFixed(1)}%예요. ${need} 이상 바꿔야 질문을 생성할 수 있어요. 공백·빈 줄 차이는 세지 않아요.`;
}
function renderChange() {
  const chip = $('edChange'); const c = S.change;
  if (!c || S.stage !== 'assignments') { chip.classList.add('hidden'); return; }
  const need = `${Math.round(c.min * 100)}%`;
  if (!c.ok) { chip.textContent = `바뀐 분량 ${c.identical ? '0%' : pct(c.ratio)} · 생성·제출하려면 ${need} 이상`; chip.className = 'chip xs chip-orange'; }
  else { chip.textContent = `바뀐 분량 ${pct(c.ratio)} · 생성·제출 가능`; chip.className = 'chip xs chip-green'; }
  chip.title = '공백·빈 줄 차이는 세지 않아요';
}
function renderDirty() {
  $('dirtyChip').classList.toggle('hidden', !isDirty());
  renderTabs();
}
async function saveAll() {
  const dirty = S.state.chain.map((c) => c.id).filter((id) => isDirty(id));
  if (!dirty.length) { toast('저장할 변경이 없어요'); return true; }
  const fmt = dirty.flatMap((id) => formatChanged(id, S.current[id]).map((k) => `${chainById(id).no} ${k}`));
  if (fmt.length) {
    const ok = await confirmBox('형식 영역이 바뀌었어요', `${fmt.join(', ')} 영역이 v9.0.0과 달라요. 출력 형식이 깨지면 생성이 실패하고 그 질문은 0점이 돼요. 그래도 저장할까요?`, '저장');
    if (!ok) return false;
  }
  try {
    for (const id of dirty) {
      const text = S.current[id];
      await api('/api/prompts/save', { id, text });
      S.saved[id] = text;
    }
  } catch (e) { toast(e.message); return false; }
  S.lastSavedAt = new Date();
  $('savedText').textContent = `마지막 저장 ${hm(S.lastSavedAt)}`;
  renderDirty();
  toast(isRunning() ? '저장했어요. 진행 중인 생성에는 시작할 때의 프롬프트가 쓰여요' : '저장했어요');
  return true;
}
async function revertStage() {
  const c = chainById(S.stage);
  const ok = await confirmBox('v9.0.0으로 되돌릴까요?', `${c.no} ${c.name} 프롬프트를 v9.0.0 원문으로 바꿔요. 저장해야 파일에 반영돼요.`, '되돌리기');
  if (!ok) return;
  const base = S.prompts[S.stage].base;
  if (S.diff) S.models[S.stage].mod.setValue(base);
  else { $('fallbackEditor').value = base; S.current[S.stage] = base; updateEditorMeta(); }
}

/* ---------------- 상단·탭·배너·오른쪽 ---------------- */
function renderTop() {
  const st = S.state; const u = st.usage;
  $('deadlineChip').textContent = st.deadline;
  $('remainChip').textContent = `남은 질문 ${u.remaining}/${u.limit}`;
  $('branchChip').textContent = st.git.branch ? `브랜치 ${st.git.branch}` : 'git 저장소 아님';
  const g = $('genBtn');
  if (isRunning()) { const r = st.running; g.disabled = true; g.classList.remove('blocked'); g.textContent = `생성 중 ${r.judgeDone}/${r.total}`; }
  else {
    const notReady = !(st.branchOk && st.key.present);
    const blocked = notReady || !!(S.change && !S.change.ok);
    g.disabled = false; g.textContent = '질문 생성';
    g.classList.toggle('blocked', blocked);
    g.title = notReady ? '화면 맨 위 시작 준비를 먼저 마쳐 주세요' : (blocked ? blockMsg(S.change) : '');
  }
}
function renderTabs() {
  const box = $('tabs'); box.innerHTML = '';
  S.state.chain.forEach((c) => {
    const b = document.createElement('button'); b.className = `tab ${c.id === S.stage ? 'on' : ''}`;
    b.innerHTML = `${esc(c.no)} ${esc(c.name)}<span class="st">${c.scored ? '채점 대상' : '점수 미반영'}</span>${isDirty(c.id) ? '<span class="dot" title="저장하지 않은 변경">●</span>' : ''}`;
    b.title = c.desc;
    b.onclick = () => { S.stage = c.id; setStageModel(); };
    box.appendChild(b);
  });
}
const bannerKeys = new Set();
function showBanner(text, tone, key, actionLabel, action) {
  if (key && bannerKeys.has(key)) return;
  const b = document.createElement('div'); b.className = `banner ${tone === 'red' ? 'red' : ''}`; b.textContent = text;
  if (key) { b.dataset.key = key; bannerKeys.add(key); }
  if (actionLabel) { const btn = document.createElement('button'); btn.className = 'btn xs'; btn.style.marginLeft = '12px'; btn.textContent = actionLabel; btn.onclick = action; b.appendChild(btn); }
  $('banners').appendChild(b);
}
function renderBanners() {
  document.querySelectorAll('.banner[data-dyn]').forEach((el) => { bannerKeys.delete(el.dataset.key); el.remove(); });
  const st = S.state; const add = (text, tone, key, a, f) => { showBanner(text, tone, key, a, f); const el = document.querySelector(`.banner[data-key="${key}"]`); if (el) el.dataset.dyn = '1'; };
  if (!st.git.repo) add('git 저장소가 아니에요. 운영 레포를 클론한 폴더에서 실행해 주세요.', 'red', 'repo');
  if (st.usage.error) add(st.usage.error, 'red', 'ledger');
  if (st.push && st.push.state === 'failed') add('실행 기록을 개인 브랜치에 올리지 못했어요. 다음 실행 때 다시 올려요.', 'org', 'push', '지금 다시 올리기', async () => { const r = await api('/api/push', {}); toast(r.message); refreshState(); });
}
function sameBase(r) {
  if (r.baselineSame != null) return r.baselineSame;
  const ca = S.state.baseline.caseAvg || {}; const ids = r.caseIds || [];
  const v = ids.map((c) => ca[c]).filter((x) => x != null);
  return v.length && v.length === ids.length ? Math.round((v.reduce((a, b) => a + b, 0) / v.length) * 10) / 10 : null;
}
function renderPanel() {
  const st = S.state; const u = st.usage; const r = st.latest; const base = st.baseline;
  const body = $('lastBody');
  if (!r) { $('lastMeta').textContent = '아직 실행한 적이 없어요'; body.innerHTML = '<p class="sub sm">질문 생성으로 첫 결과를 만들어 보세요.</p>'; $('railResult').textContent = '-'; }
  else {
    const full = r.evalScore != null;
    $('lastMeta').textContent = `${fmtTime(r.finishedAt)} · 질문 ${r.cases * st.qpc}개`;
    $('railResult').textContent = full ? r.evalScore : (r.questionAvg ?? '-');
    const rf = Object.values(r.ruleFails || {}).reduce((a, b) => a + b, 0);
    const top = Object.entries(r.failCounts || {})[0];
    const head = full
      ? `<div class="kv"><span class="sub">평가 점수</span><b class="h20">${r.evalScore}</b></div>
         <div class="kv"><span class="sub">기준 점수 (v9.0.0)</span><span class="b6">${base.ready ? `${base.mean} · 패널티 기준 ${base.threshold}` : '산출 예정'}</span></div>`
      : `<div class="kv"><span class="sub">질문 평균 · 케이스 ${r.cases}개</span><b class="h20">${r.questionAvg ?? '-'}</b></div>
         <div class="kv"><span class="sub">같은 케이스 기준 (v9.0.0)</span><span class="b6">${sameBase(r) ?? '-'}</span></div>
         <p class="muted xs">평가 점수는 전체 케이스로 실행해야 나와요</p>`;
    body.innerHTML = `${head}<div class="divider"></div>
      ${st.rubric.map((d) => `<div class="kv"><span class="sub">${d.id} ${esc(d.name)}</span><span class="v">${r.dimAvg?.[d.id] ?? '-'} / ${d.weight}</span></div>`).join('')}
      <div class="kv"><span class="sub">규칙 진단</span><span class="sub sm b5">위반 ${rf}건</span></div>
      <div class="kv"><span class="sub">많이 걸린 유형</span><span class="org sm b6">${top ? `${top[0]} ${esc(failName(top[0]))} ×${top[1]}` : '없음'}</span></div>
      <button class="btn sm full" data-view="result">결과 보기</button>`;
  }
  const cycles = Math.floor(u.remaining / (10 * st.qpc));
  $('remainBig').textContent = `${u.remaining} / ${u.limit}`;
  $('remainSub').textContent = `사이클 ${cycles}번 남음`;
  $('railRemain').textContent = u.remaining;
  $('remainBar').style.width = `${Math.min(100, ((u.used || 0) / u.limit) * 100)}%`;
  $('remainText').textContent = '생성을 시작하면 바로 차감되고, 기록은 runs/에 남아 개인 브랜치에 자동으로 올라가요.';
}
function applyPanel() {
  $('panel').classList.toggle('hidden', !UI.panel);
  $('railToggle').classList.toggle('on', UI.panel);
  document.querySelectorAll('.acc').forEach((a) => a.classList.toggle('open', !!UI.open[a.dataset.sec]));
  document.querySelectorAll('.rail-item').forEach((b) => b.classList.toggle('on', UI.panel && !!UI.open[b.dataset.rail]));
  saveUI();
}
function renderCaseChips() {
  const box = $('caseChips'); box.innerHTML = '';
  S.cases.forEach((c) => { const b = document.createElement('button'); b.className = 'case-chip'; b.textContent = c.label; b.onclick = () => openCase(c.caseId); box.appendChild(b); });
}
/* ---------------- 시작 준비 카드 ---------------- */
function setupDismissed() { try { return localStorage.getItem('contest.setupDone') === '1'; } catch { return false; } }
function renderSetup(force) {
  const st = S.state; const card = $('setupCard'); const err = S.setupErr || {};
  const branchDone = !!st.branchOk; const keyDone = !!st.key.present && st.key.checked !== 'fail';
  const all = branchDone && keyDone;
  if (!st.git.repo || (all && setupDismissed())) { card.classList.add('hidden'); S.setupSig = null; return; }
  const sig = JSON.stringify([branchDone, keyDone, st.git.branch, st.key.last4, err.branch, err.key]);
  if (!force && sig === S.setupSig) return;
  S.setupSig = sig;
  const left = [branchDone, keyDone].filter((x) => !x).length;
  const chip = (t, c) => `<span class="chip xs ${c}">${t}</span>`;
  const b = branchDone
    ? `<div class="su-step done"><div class="t">① 개인 브랜치 ${chip('완료', 'chip-done')}</div><b class="h15">${esc(st.git.branch)}</b><p class="sub xs">생성 기록이 이 브랜치에 쌓이고 원격에 자동으로 올라가요.</p></div>`
    : `<div class="su-step ${err.branch ? 'error' : 'pending'}"><div class="t">① 개인 브랜치 ${chip(err.branch ? '확인 필요' : '필요', err.branch ? 'chip-red' : 'chip-orange')}</div>
        <p class="sub xs">실행 기록을 올릴 내 브랜치예요. GitHub 로그인 이름으로 만들어요.</p>
        <div class="row gap8 center"><label class="su-field ${err.branch ? 'error' : ''}"><span>${esc(st.branchPrefix)}</span><input id="suName" value="${esc(st.setup.suggested || '')}" placeholder="영문 이름" spellcheck="false" autocomplete="off"></label><button class="btn btn-primary sm" id="suBranch">만들기</button></div>
        ${err.branch ? `<p class="red xs b6">${esc(err.branch)}</p>` : '<button class="link-btn" id="suRename" style="align-self:flex-start">다른 이름으로 하기</button>'}</div>`;
  const k = keyDone
    ? `<div class="su-step done"><div class="t">② API 키 ${chip('완료', 'chip-done')}</div><b class="h15">연결됨 · 끝자리 ${esc(st.key.last4 || '')}</b><p class="sub xs">이 PC의 .env에 저장했어요. 바꾸려면 설정 창에서 키 바꾸기를 눌러요.</p></div>`
    : `<div class="su-step ${err.key ? 'error' : 'pending'}"><div class="t">② API 키 ${chip(err.key ? '확인 필요' : '필요', err.key ? 'chip-red' : 'chip-orange')}</div>
        <p class="sub xs">DM으로 받은 키를 붙여 넣어요. 이 PC의 .env에만 저장되고 화면과 대화에 다시 보이지 않아요.</p>
        <div class="row gap8 center"><label class="su-field ${err.key ? 'error' : ''}"><input id="suKey" type="password" autocomplete="off" spellcheck="false" placeholder="sk-로 시작하는 키를 붙여 넣으세요"></label><button class="btn btn-primary sm" id="suKeySave">저장하고 확인</button></div>
        ${err.key ? `<p class="red xs b6">${esc(err.key)}</p>` : ''}</div>`;
  const d = all
    ? `<div class="su-step done"><div class="t">③ 질문 생성 ${chip('완료', 'chip-done')}</div><b class="h15">준비가 끝났어요</b><p class="sub xs">③ 프롬프트를 3% 이상 고친 뒤 질문 생성을 눌러요.</p></div>`
    : `<div class="su-step wait"><div class="t muted">③ 질문 생성 ${chip('대기', 'chip-gray')}</div><p class="muted xs">①②가 끝나면 편집기에서 질문을 생성할 수 있어요.</p></div>`;
  card.innerHTML = `<div class="su-head"><b class="h16">시작 준비</b>${left ? chip(`${left}단계 남았어요`, 'chip-orange') : chip('준비 끝', 'chip-green')}
    <span class="sub sm grow">이 PC에서 한 번만 하면 돼요</span>${all ? '<button class="btn btn-ghost sm" id="suClose">카드 닫기</button>' : ''}</div>
    <div class="su-steps">${b}${k}${d}</div>`;
  card.classList.remove('hidden');
  const on = (id, ev, fn) => { const el = $(id); if (el) el[ev] = fn; };
  on('suBranch', 'onclick', setupBranch);
  on('suName', 'onkeydown', (e) => { if (e.key === 'Enter') setupBranch(); });
  on('suRename', 'onclick', () => { const i = $('suName'); i.focus(); i.select(); });
  on('suKeySave', 'onclick', setupKey);
  on('suKey', 'onkeydown', (e) => { if (e.key === 'Enter') setupKey(); });
  on('suClose', 'onclick', () => { try { localStorage.setItem('contest.setupDone', '1'); } catch { /* 다음에 다시 보임 */ } card.classList.add('hidden'); });
  if (S.diff) setTimeout(() => S.diff.layout(), 0);
}
async function setupBranch() {
  const name = $('suName').value.trim(); $('suBranch').disabled = true;
  try { const r = await api('/api/setup/branch', { name }); S.setupErr = { ...(S.setupErr || {}), branch: null }; toast(`${r.branch} · ${r.message}`); }
  catch (e) { S.setupErr = { ...(S.setupErr || {}), branch: e.message }; }
  await refreshState(); renderSetup(true);
}
async function setupKey() {
  const input = $('suKey'); const key = input.value;
  if (!key.trim()) { input.focus(); return; }
  $('suKeySave').disabled = true;
  try { const r = await api('/api/key/save', { key }); S.setupErr = { ...(S.setupErr || {}), key: null }; toast(r.message); }
  catch (e) { S.setupErr = { ...(S.setupErr || {}), key: e.message }; }
  input.value = '';
  await refreshState(); renderSetup(true);
}

async function refreshState() {
  try { S.state = await api('/api/state'); } catch { return; }
  renderTop(); renderPanel(); renderBanners(); renderSetup();
  if (isRunning() && !S.poll) startPolling();
}

/* ---------------- 질문 생성 창 ---------------- */
async function openGen(prefill) {
  if (isRunning()) { SN.expanded = true; renderSnackRunning(S.state.running); toast('이미 생성 중이에요'); return; }
  const st = S.state;
  S.genScope = prefill?.scope || 'all';
  S.genPick = new Set(prefill?.scope === 'pick' ? prefill.cases : []);
  const ms = $('genModel'); ms.innerHTML = '';
  Object.keys(st.models).forEach((m) => ms.add(new Option(m, m)));
  if (st.allowCustomModel) ms.add(new Option('직접 입력', '__custom'));
  const model = prefill?.model || st.local.defaultModel;
  if (st.models[model]) ms.value = model; else if (st.allowCustomModel) { ms.value = '__custom'; $('genCustomModel').value = model; }
  fillEfforts(prefill?.effort || st.local.defaultEffort);
  $('genJudge').textContent = `${st.judge.label} · ${st.judge.model} · ${st.judge.effort}`;
  const pc = $('pickChips'); pc.innerHTML = '';
  S.cases.forEach((c) => {
    const b = document.createElement('button'); b.type = 'button'; b.className = `pick-chip ${S.genPick.has(c.caseId) ? 'on' : ''}`; b.textContent = c.label;
    b.onclick = (e) => { e.preventDefault(); S.genPick.has(c.caseId) ? S.genPick.delete(c.caseId) : S.genPick.add(c.caseId); b.classList.toggle('on'); renderGen(); };
    pc.appendChild(b);
  });
  document.querySelectorAll('input[name=scope]').forEach((r) => { r.checked = r.value === S.genScope; });
  $('genErr').classList.add('hidden');
  S.genChange = null;
  try { S.genChange = await api('/api/prompts/change', { id: 'assignments', text: S.current.assignments }); } catch { /* 표시만 생략 */ }
  renderGen();
  openModal('genModal');
}
function fillEfforts(pref) {
  const m = $('genModel').value; const es = $('genEffort'); es.innerHTML = '';
  const list = S.state.models[m] || ['none', 'low', 'medium', 'high', 'xhigh'];
  list.forEach((e) => es.add(new Option(`추론 ${e}`, e)));
  es.value = list.includes(pref) ? pref : (list.includes('medium') ? 'medium' : list[0]);
  $('genCustomModel').classList.toggle('hidden', m !== '__custom');
}
async function renderGen() {
  const st = S.state; const n = S.genScope === 'all' ? S.cases.length : S.genPick.size; const q = n * st.qpc;
  $('scopeAll').classList.toggle('on', S.genScope === 'all'); $('scopePick').classList.toggle('on', S.genScope === 'pick');
  $('sumQ').textContent = `질문 ${q}개`;
  const after = st.usage.remaining - q;
  $('sumRemain').textContent = `${after} / ${st.usage.limit}`; $('sumRemain').className = after < 0 ? 'red' : 'blue';
  $('genDirtyWarn').classList.toggle('hidden', !isDirty());
  $('genStart').textContent = isDirty() ? '저장하고 생성하기' : '생성하기';
  let err = '';
  if (!st.git.repo) err = 'git 저장소에서만 생성할 수 있어요.';
  else if (!st.branchOk) err = `개인 브랜치(${st.branchPrefix}영문이름)에서만 생성할 수 있어요. 시작 준비 카드에서 브랜치를 먼저 만들어 주세요.`;
  else if (!st.key.present) err = 'API 키가 비어 있어요. 시작 준비 카드나 설정 창에서 DM으로 받은 키를 저장해 주세요.';
  else if (S.genChange && !S.genChange.ok) err = blockMsg(S.genChange);
  else if (n === 0) err = '실행할 케이스를 하나 이상 골라 주세요.';
  else if (after < 0) err = `남은 질문이 ${st.usage.remaining}개라 ${q}개를 만들 수 없어요.`;
  $('genStart').disabled = !!err;
  $('genErr').textContent = err; $('genErr').classList.toggle('hidden', !err);
  $('sumTime').textContent = '…';
  try {
    const e = await api(`/api/estimate?cases=${n || 1}`);
    S.genEst = e.seconds;
    $('sumTime').textContent = e.seconds ? `약 ${Math.max(1, Math.round(e.seconds / 60))}분` : '기준선 실측 후 표시';
  } catch { S.genEst = null; $('sumTime').textContent = '기준선 실측 후 표시'; }
}
async function startGen() {
  if (S.genChange && !S.genChange.ok) return;
  if (isDirty() && !(await saveAll())) return;
  const m = $('genModel').value; const model = m === '__custom' ? $('genCustomModel').value.trim() : m;
  const body = { scope: S.genScope, cases: [...S.genPick], model, effort: $('genEffort').value };
  $('genStart').disabled = true;
  try {
    const snap = await api('/api/run', body);
    closeModal('genModal');
    S.state.running = snap; renderTop();
    SN.expanded = false; SN.stay = false; SN.doneRunId = null; SN.est = S.genEst;
    renderSnackRunning(snap); startPolling();
  } catch (e) {
    $('genErr').textContent = e.message; $('genErr').classList.remove('hidden'); $('genStart').disabled = false;
  }
}

/* ---------------- 진행 스낵바 ---------------- */
const ICON = {
  check: '<svg class="sn-ic" viewBox="0 0 24 24" fill="none" stroke="#34D399" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  alert: '<svg class="sn-ic" viewBox="0 0 24 24" fill="none" stroke="#F87171" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg>',
  stop: '<svg class="sn-ic" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  x: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
};
function showSnack(html, mode) { const s = $('snack'); s.innerHTML = html; s.classList.remove('hidden'); SN.mode = mode; }
function hideSnack() { clearInterval(SN.timer); SN.timer = null; $('snack').classList.add('hidden'); SN.mode = null; }
function leftText(r) {
  if (!SN.est || !r.startedAt) return '';
  const rem = SN.est - (Date.now() - new Date(r.startedAt).getTime()) / 1000;
  if (rem > 60) return `약 ${Math.ceil(rem / 60)}분 남음`;
  return rem > 0 ? '1분 안에 끝나요' : '곧 끝나요';
}
function caseState(c) {
  if (c.gen === 'skip') return ['건너뜀', ''];
  if (c.gen === 'wait') return ['대기', ''];
  if (c.gen === 'running') return ['생성 중', 'on'];
  if (c.gen === 'error') return ['생성 실패', 'bad'];
  if (c.judge === 'running') return ['채점 중', 'on'];
  if (c.judge === 'done') return ['채점 완료', 'ok'];
  if (c.judge === 'error') return ['채점 실패', 'bad'];
  if (c.judge === 'skip') return ['출력 오류', 'bad'];
  return ['채점 대기', ''];
}
function renderSnackRunning(r) {
  const phase = r.genDone < r.total ? `① 질문 생성 ${r.genDone}/${r.total} 케이스` : (r.judgeDone < r.total ? `③ AI 채점 ${r.judgeDone}/${r.total} 케이스` : '마무리하는 중');
  const p = r.total ? ((r.genDone + r.judgeDone) / (2 * r.total)) * 100 : 0;
  const left = leftText(r);
  let html = `<div class="sn-h"><span class="spin"></span><span class="t">질문 생성 중</span>
    <button class="sn-btn" data-sn="toggle">${SN.expanded ? '접기' : '자세히'}</button><button class="sn-btn red" data-sn="stop">중지</button></div>
    <div class="sn-bar"><i style="width:${p}%"></i></div>
    <div class="sn-sub">${phase}${r.startedAt ? ` · ${hm(r.startedAt)} 시작` : ''}${left ? ` · ${left}` : ''}</div>`;
  if (SN.expanded) {
    const step = (t, done, on) => `<span class="sn-chip ${done ? 'ok' : (on ? 'on' : '')}">${done ? '✓ ' : ''}${t}</span>`;
    html += `<div class="sn-steps">${step(`질문 생성 ${r.genDone}/${r.total}`, r.genDone === r.total, r.genDone < r.total)}${step('규칙 검사', r.checkDone === r.total, false)}${step(`AI 채점 ${r.judgeDone}/${r.total}`, r.judgeDone === r.total, r.genDone === r.total)}</div>
      <div class="sn-list">${r.cases.map((c) => { const [t, k] = caseState(c); return `<div class="r"><span>${esc(c.label)}</span><span class="sn-chip ${k}">${t}</span></div>`; }).join('')}</div>
      <div class="sn-sub">생성은 시작할 때 저장된 프롬프트로 진행돼요. 그동안 편집기에서 다음 수정을 이어 가도 돼요.</div>`;
  }
  showSnack(html, 'run');
}
function doneLine(res) {
  const base = S.state.baseline;
  if (res.evalScore != null) {
    if (!base.ready) return `평가 점수 ${res.evalScore}`;
    const d = Math.round((res.evalScore - base.mean) * 10) / 10;
    return `평가 점수 ${res.evalScore} · 기준 ${base.mean}보다 ${Math.abs(d)}점 ${d >= 0 ? '높아요' : '낮아요'}`;
  }
  const same = res.baselineSame ?? sameBase({ ...res, caseIds: res.cases });
  return `질문 평균 ${res.questionAvg ?? '-'} (케이스 ${res.cases.length}개)${same != null ? ` · 같은 케이스 기준 ${same}` : ''}`;
}
function renderSnackDone(res) {
  SN.res = res;
  clearInterval(SN.timer); SN.timer = null;
  const onResult = !$('view-result').classList.contains('hidden');
  const head = `<div class="sn-h">${ICON.check}<span class="t">질문 생성 완료</span><button class="sn-btn" data-sn="close" title="닫기">${ICON.x}</button></div>
    <div class="sn-txt">${esc(doneLine(res))}</div><div class="sn-bar ok"><i style="width:100%"></i></div>`;
  if (onResult) {
    showSnack(`${head}<div class="sn-sub">결과 화면을 새로 불러왔어요.</div>`, 'done');
    loadResult(); SN.timer = setTimeout(hideSnack, 6000); return;
  }
  let n = 3;
  const paint = () => {
    const blocked = SN.stay || isDirty();
    const foot = blocked
      ? `<span class="sn-sub">${isDirty() ? '저장하지 않은 변경이 있어 결과 화면으로 넘어가지 않아요' : '이 화면에 머물러요'}</span><button class="sn-pri" data-sn="result">결과 보기</button>`
      : `<span class="sn-sub">${n}초 뒤 결과 화면으로 이동해요</span><span class="row gap6"><button class="sn-btn" data-sn="stay">머무르기</button><button class="sn-pri" data-sn="result">결과 보기</button></span>`;
    showSnack(`${head}<div class="sn-foot">${foot}</div>`, 'done');
    return blocked;
  };
  if (paint()) return;
  SN.timer = setInterval(() => {
    n -= 1;
    if (paint()) { clearInterval(SN.timer); SN.timer = null; return; }
    if (n <= 0) { clearInterval(SN.timer); SN.timer = null; goResult(); }
  }, 1000);
}
function renderSnackProblem(title, text, icon) {
  showSnack(`<div class="sn-h">${icon}<span class="t">${esc(title)}</span><button class="sn-btn" data-sn="close" title="닫기">${ICON.x}</button></div>
    <div class="sn-txt">${esc(text)}</div><div class="sn-foot"><span></span><button class="sn-pri" data-sn="result">결과 보기</button></div>`, 'problem');
}
async function onFinished(r) {
  if (SN.doneRunId === r.runId) return;
  SN.doneRunId = r.runId;
  if (r.status === 'error') { renderSnackProblem('생성을 시작하지 못했어요', r.message || '다시 시도해 주세요.', ICON.alert); return; }
  let res = null;
  try { res = await api(`/api/result?id=${encodeURIComponent(r.runId)}`); } catch { /* 결과 없음 */ }
  if (!res) { renderSnackProblem('결과를 불러오지 못했어요', '결과 화면에서 다시 확인해 주세요.', ICON.alert); return; }
  if (r.status === 'stopped') { renderSnackProblem('생성을 멈췄어요', `질문 ${res.charged}개가 남은 질문에서 빠졌어요. 평가 점수는 계산하지 않았어요.`, ICON.stop); return; }
  const failed = res.items.filter((c) => c.genError || c.judgeError);
  if (failed.length) {
    renderSnackProblem('생성하지 못한 케이스가 있어요', `${failed.map((c) => c.label.split(' ')[0]).join('·')} 케이스가 실패해 평가 점수를 계산하지 못했어요. 만든 질문 수는 남은 질문에서 빠졌어요. 다시 실행해 주세요.`, ICON.alert);
    return;
  }
  renderSnackDone(res);
}
function goResult() { hideSnack(); route('result'); }
function startPolling() {
  stopPolling();
  S.poll = setInterval(pollOnce, 1200);
  pollOnce();
}
function stopPolling() { if (S.poll) clearInterval(S.poll); S.poll = null; }
async function pollOnce() {
  let r; try { r = await api('/api/run/current'); } catch { return; }
  if (!r) { stopPolling(); return; }
  S.state.running = r; renderTop();
  if (r.status === 'running') { renderSnackRunning(r); return; }
  stopPolling(); await refreshState(); await onFinished(r);
}
async function stopRun() {
  const ok = await confirmBox('생성을 멈출까요?', '이미 보낸 요청은 끝까지 진행되고, 만든 질문 수는 남은 질문에서 빠져요. 평가 점수는 계산하지 않아요.', '중지');
  if (ok) { await api('/api/run/stop', {}); toast('멈추는 중이에요'); }
}

/* ---------------- 결과 ---------------- */
async function loadResult(id) {
  let r = null;
  try { r = await api(`/api/result${id ? `?id=${encodeURIComponent(id)}` : ''}`); } catch { r = null; }
  S.result = r;
  $('resultEmpty').classList.toggle('hidden', !!r); $('resultBody').classList.toggle('hidden', !r);
  if (r) renderResult(r);
}
function scoreClass(v) { return v == null ? '' : v >= 80 ? 'score-good' : v < 70 ? 'score-bad' : ''; }
function renderResult(r) {
  const st = S.state; const n = r.cases.length; const base = st.baseline; const full = r.evalScore != null;
  $('rScope').textContent = r.scope === 'all' ? `한 사이클 · 질문 ${n * st.qpc}개` : `케이스 ${n}개 · 질문 ${n * st.qpc}개`;
  $('rMeta').textContent = `${fmtTime(r.finishedAt)} · ${r.model} · ${r.effort || ''} · 저장 커밋 ${r.head || '-'}${r.status === 'stopped' ? ' · 중지됨' : ''}`;
  $('rFile').textContent = `runs/${r.runId}.json에 기록됨`;
  const ps = st.push || {}; const pmap = { pushed: ['개인 브랜치에 올라감', 'chip-green'], failed: ['푸시 실패 · 다음 실행 때 다시 올려요', 'chip-orange'], pushing: ['올리는 중', 'chip-blue'], 'no-remote': ['원격 없음 · 커밋만 함', 'chip-gray'] };
  const pp = pmap[ps.state]; $('rPush').classList.toggle('hidden', !pp); if (pp) { $('rPush').textContent = pp[0]; $('rPush').className = `chip sm ${pp[1]}`; }
  const same = sameBase({ ...r, caseIds: r.cases });
  $('rScoreLabel').textContent = full ? '평가 점수' : `질문 평균 · 케이스 ${n}개`;
  const shown = full ? r.evalScore : r.questionAvg;
  $('rScore').textContent = shown ?? '–'; $('rScore').className = `h44 ${scoreClass(shown)}`;
  $('rBaseLabel').textContent = full ? '기준 점수 (v9.0.0)' : '같은 케이스 기준 (v9.0.0)';
  $('rBase').textContent = full ? (base.ready ? `${base.mean} · 패널티 기준 ${base.threshold}` : '산출 예정') : (same ?? '산출 예정');
  $('rScoreNote').textContent = full
    ? '질문 20개 AI 채점 평균이에요. 기준 점수는 v9.0.0을 3회 생성·채점한 평균이고, 이보다 낮으면 패널티가 있어요.'
    : `${r.evalNote || ''} 비교는 같은 케이스의 기준선 평균과 해요.`;
  $('rVariance').textContent = base.ready ? `같은 프롬프트도 실행마다 점수가 달라질 수 있어요(기준선 흔들림 폭 ${base.spread}).` : '';
  const fails = Object.entries(r.failCounts || {});
  const failText = fails.length ? fails.map(([k, v]) => `${k} ${failName(k)} ×${v}`).join(' · ') : '실패 유형 없음';
  $('rDims').innerHTML = st.rubric.map((d) => {
    const v = r.dimAvg?.[d.id];
    const desc = d.id === 'J2' ? `<p class="xs org">${esc(failText)}</p>` : `<p class="xs sub">${esc(d.desc)}</p>`;
    return `<div class="card dim"><div class="row gap6 center"><span class="id">${d.id}</span><b class="sm">${esc(d.name)}</b></div>
      <div class="row gap6 end"><b class="h22">${v ?? '-'}</b><span class="muted sm">/ ${d.weight}</span></div>
      <div class="bar ${d.id === 'J2' ? 'org' : ''}"><i style="width:${v == null ? 0 : (v / d.weight) * 100}%"></i></div>${desc}</div>`;
  }).join('');
  const rf = Object.entries(r.ruleFails || {}); const rfTotal = rf.reduce((a, [, v]) => a + v, 0);
  $('rRules').innerHTML = `<b class="sm">규칙 진단 16개</b><span class="chip chip-gray sm">위반 ${rfTotal}건</span>
    ${rf.map(([k, v]) => `<span class="chip chip-orange sm">${k} ${esc(ruleName(k))} ×${v}</span>`).join('')}
    <button class="link" data-open="criteriaModal">기준 보기 →</button>`;
  const head = '<div class="tr head"><span>케이스</span><span>지원자</span><span>평가 주제</span><span>점수 · 기준</span><span>실패 유형</span><span>규칙 진단</span><span></span></div>';
  $('rTable').innerHTML = head + r.items.map((c, i) => caseRow(c, i === 0)).join('');
  $('rTable').querySelectorAll('[data-toggle]').forEach((b) => { b.onclick = () => toggleRow(b.dataset.toggle); });
  $('rTable').querySelectorAll('[data-drawer]').forEach((b) => { b.onclick = () => toggleDrawer(b.dataset.drawer); });
  if (S.drawerCase) markDrawer();
  $('rTable').querySelectorAll('[data-raw]').forEach((b) => { b.onclick = () => { $(`raw-${b.dataset.raw}`).classList.toggle('hidden'); }; });
}
function caseRow(c, open) {
  const codes = {}; c.items.forEach((it) => (it.failCodes || []).forEach((k) => { codes[k] = (codes[k] || 0) + 1; }));
  const ct = Object.entries(codes).map(([k, v]) => `${k} ×${v}`).join(' · ') || '없음';
  const ran = !!(c.outputText || c.genError);
  const rules = !ran || c.genError ? '-' : (c.checks.failed?.length ? c.checks.failed.map((k) => `${k} ×1`).join(' · ') : '위반 0');
  const avg = c.genError ? '실패' : (c.avg ?? '-');
  const b = c.baselineAvg ?? (S.state.baseline.caseAvg || {})[c.caseId];
  const drIcon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M15 3v18"/><path d="m10 15-3-3 3-3"/></svg>';
  return `<div class="tr ${open ? 'open' : ''}" id="row-${c.caseId}">
    <span class="case-cell"><b>${esc(c.label)}</b><button class="dr-btn" data-drawer="${c.caseId}" title="공고·지원자 정보 보기">${drIcon}</button></span>
    <span class="sub">${esc(c.who)}</span><span>${esc(c.category)}</span>
    <span><b class="${scoreClass(c.avg)}">${avg}</b>${b != null ? `<span class="muted xs"> · 기준 ${b}</span>` : ''}</span>
    <span class="sm b5 ${ct === '없음' || !ran ? 'muted' : 'org'}">${ran ? ct : '-'}</span>
    <span class="sm b5 ${rules === '위반 0' || rules === '-' ? 'muted' : 'org'}">${rules}</span>
    <button class="toggle" data-toggle="${c.caseId}">${open ? '접기 ▴' : '펼치기 ▾'}</button></div>
    <div class="expand ${open ? '' : 'hidden'}" id="exp-${c.caseId}">${c.items.map((it, i) => qcard(c, it, i)).join('')}</div>
    <div class="${open ? '' : 'hidden'}" id="expf-${c.caseId}">${c.outputText ? `<div style="padding:0 20px 12px"><button class="link-btn" data-raw="${c.caseId}">출력 원문 보기</button></div><pre class="raw hidden" id="raw-${c.caseId}">${esc(c.outputText)}</pre>` : ''}${c.genError ? `<div class="err-row" style="margin:0 20px 16px">생성 실패: ${esc(c.genError)}</div>` : ''}${c.judgeError ? `<div class="err-row" style="margin:0 20px 16px">채점 실패: ${esc(c.judgeError)}</div>` : ''}</div>`;
}
function toggleRow(id) {
  const row = $(`row-${id}`); const open = !row.classList.contains('open');
  row.classList.toggle('open', open); $(`exp-${id}`).classList.toggle('hidden', !open); $(`expf-${id}`).classList.toggle('hidden', !open);
  row.querySelector('.toggle').textContent = open ? '접기 ▴' : '펼치기 ▾';
}
function qcard(c, it, i) {
  const st = S.state;
  const chips = st.rubric.map((d) => `<span class="chip chip-gray sm">${d.id} ${it.points?.[d.id] != null ? Math.round(it.points[d.id] * 10) / 10 : '-'}/${d.weight}</span>`).join('');
  const fail = it.failCodes?.length ? it.failCodes.map((k) => `<span class="chip chip-orange sm">${k} ${esc(failName(k))}</span>`).join('') : (it.points ? '<span class="chip chip-green sm">실패 유형 없음</span>' : '');
  const reasons = it.reasons || {};
  const others = ['J1', 'J3', 'J4'].filter((k) => reasons[k]).map((k) => `<li>${k} · ${esc(reasons[k])}</li>`).join('');
  const failed = c.checks.failed || [];
  const ruleLine = c.genError ? '' : (failed.length ? `<span class="org xs b6">규칙 위반 ${failed.map((k) => `${k} ${esc(ruleName(k))}`).join(', ')}</span>` : '<span class="muted xs b5">규칙 진단 16/16 통과</span>');
  return `<div class="qcard">
    <div class="row between center gap12"><span class="sm b6 sub">평가 요소 ${i + 1} · ${esc(it.itemName)}</span><b class="h22 ${scoreClass(it.total)}">${it.total ?? '-'}</b></div>
    ${it.note ? `<div class="warn-row">${esc(it.note)}</div>` : ''}
    <div class="qbox"><span class="lbl">이번 질문</span><div class="q">${esc(it.question || '(질문 없음)')}</div></div>
    <span class="base-lbl">v9.0.0 질문 (비교용)</span><div class="base-q">${esc(it.baselineQuestion || '기준선 생성 후 표시')}</div>
    ${it.points ? `<div class="wrap" style="margin-top:0">${chips}${fail}</div>` : ''}
    ${reasons.J2 ? `<div class="reason">근거 · ${esc(reasons.J2)}${others ? `<ul>${others}</ul>` : ''}</div>` : ''}
    ${ruleLine ? `<div class="case-foot">${ruleLine}</div>` : ''}</div>`;
}

/* 결과 화면의 공고·지원자 정보 서랍: 질문을 보면서 같은 케이스의 이력과 공고를 함께 본다 */
function toggleDrawer(caseId) { if (S.drawerCase === caseId) closeDrawer(); else openDrawer(caseId); }
function markDrawer() {
  document.querySelectorAll('.dr-btn').forEach((b) => b.classList.toggle('on', b.dataset.drawer === S.drawerCase));
}
function openDrawer(caseId) {
  const c = S.cases.find((x) => x.caseId === caseId); if (!c) return;
  S.drawerCase = caseId;
  const p = c.position; const li = (arr) => `<ul>${(arr || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
  const careers = (c.careers || []).map((k) => `<div class="h">경력 · ${esc(k.company)} ${esc(k.jobTitle || '')} ${esc(k.employmentType || '')} · ${esc(k.period || '')}</div><div>${esc(k.summaryLine || '')}</div>`).join('');
  const projects = (c.projects || []).map((k) => `<div class="h">프로젝트 · ${esc(k.name)} · ${esc(k.organization || '')} · ${esc(k.teamType || '')}</div><div>${esc(k.description || '')}</div>`).join('');
  const acts = (c.activities || []).map((k) => `<div class="h">활동 · ${esc(k.name)} · ${esc(k.type || '')}</div><div>${esc(k.description || '')}</div>`).join('');
  const notes = (c.notes || []).map((k) => `<div>${esc(k.content || '')}</div>`).join('');
  $('drLabel').textContent = c.label; $('drWho').textContent = c.who;
  $('drSub').textContent = `${c.caseId} · 질문을 보면서 지원자 이력과 공고를 함께 확인해요. 골든셋을 보며 프롬프트를 맞추는 것은 지양해요`;
  $('drBody').innerHTML = `
    <section class="dr-sec"><div class="row gap8 center"><span class="chip chip-dark xs">지원자</span><b class="h16">지원자 이력</b></div>
      <div class="b5">${esc(c.persona)}</div>${careers}${projects}${acts}${notes ? `<div class="h">메모</div>${notes}` : ''}
      ${c.resume ? `<details><summary>이력서 요약 보기</summary><div style="margin-top:8px">${esc(c.resume)}</div></details>` : ''}</section>
    <div class="divider"></div>
    <section class="dr-sec"><div class="row gap8 center"><span class="chip chip-dark xs">공고</span><b class="h16">공고 · ${esc(p.positionName || c.job)}</b></div>
      <div class="h">주요 업무</div>${li(p.jobDescription)}<div class="h">자격 요건</div>${li(p.requirements)}<div class="h">우대 사항</div>${li(p.preferredQualifications)}</section>
    <div class="divider"></div>
    <section class="dr-sec"><div class="row gap8 center"><span class="chip chip-solid-blue xs">평가 주제</span><b class="h16">${esc(c.category.name)}</b></div>
      <div>${esc(c.category.definition)}</div>
      ${c.items.map((it, i) => `<div class="dr-item"><b>요소 ${i + 1} · ${esc(it.name)}</b><div class="sub xs">${esc(it.definition)}</div></div>`).join('')}
      ${c.forbidden?.length ? `<b class="org">질문에 쓰면 안 되는 말 · ${c.forbidden.map(esc).join(' · ')}</b>` : ''}</section>`;
  $('drBody').scrollTop = 0;
  $('caseDrawer').classList.remove('hidden'); document.body.classList.add('drawer-open');
  markDrawer();
  const row = $(`row-${caseId}`);
  if (row && !row.classList.contains('open')) toggleRow(caseId);
  if (row) row.scrollIntoView({ block: 'start', behavior: 'smooth' });
}
function closeDrawer() {
  S.drawerCase = null;
  $('caseDrawer').classList.add('hidden'); document.body.classList.remove('drawer-open');
  markDrawer();
}

/* ---------------- 창: 기준·설정·케이스 ---------------- */
function renderCriteria() {
  const st = S.state;
  $('critRubric').innerHTML = '<div class="rr head"><span>ID</span><span>항목</span><span>배점</span><span>무엇을 보나</span></div>' +
    st.rubric.map((d) => `<div class="rr"><b class="blue">${d.id}</b><b>${esc(d.name)}</b><b>${d.weight}</b><span>${esc(d.desc)}</span></div>`).join('');
  $('critFail').innerHTML = st.failTypes.map((f) => `<div class="fr"><span class="chip chip-orange sm">${f.id}</span><b class="sm">${esc(f.name)}</b><span class="sm">${esc(f.desc)}</span></div>`).join('');
  $('critRules').innerHTML = st.rules.map(([id, n]) => `<div>${id} ${esc(n)}</div>`).join('');
}
function openSettings() {
  const st = S.state;
  const k = st.key; const box = $('keyBox');
  let chip; let msg; let cls;
  const saved = `이 PC의 .env에 저장된 키 · 끝자리 ${k.last4 || ''}`;
  if (!k.present) { chip = ['키 없음', 'chip-red']; msg = '.env에 키가 없어요. 아래에 붙여 넣어 주세요'; cls = 'bad'; }
  else if (k.checked === 'ok') { chip = ['연결됨', 'chip-solid-blue']; msg = saved; cls = 'ok'; }
  else if (k.checked === 'fail') { chip = ['확인 실패', 'chip-red']; msg = `${saved} · ${k.message || '키를 확인해 주세요'}`; cls = 'bad'; }
  else { chip = ['저장됨', 'chip-white']; msg = saved; cls = ''; }
  $('keyChip').textContent = chip[0]; $('keyChip').className = `chip sm ${chip[1]}`; $('keyMsg').textContent = msg; box.className = `key-box ${cls}`;
  $('keyEdit').classList.toggle('hidden', !!k.present); $('keyActs').classList.toggle('hidden', !k.present);
  $('keyCancel').classList.toggle('hidden', !k.present);
  $('keyErr').classList.add('hidden'); $('keyInput').value = '';
  const ms = $('setModel'); ms.innerHTML = '';
  Object.keys(st.models).forEach((m) => ms.add(new Option(m, m)));
  ms.value = st.models[st.local.defaultModel] ? st.local.defaultModel : Object.keys(st.models)[0];
  const renderEff = (pref) => {
    const list = st.models[ms.value]; const seg = $('setEffort'); seg.innerHTML = '';
    const cur = list.includes(pref) ? pref : 'medium';
    list.forEach((e) => { const b = document.createElement('button'); b.textContent = e; b.className = e === cur ? 'on' : ''; b.onclick = () => { seg.querySelectorAll('button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); }; seg.appendChild(b); });
  };
  ms.onchange = () => renderEff(st.local.defaultEffort);
  renderEff(st.local.defaultEffort);
  $('setJudge').textContent = `${st.judge.label.replace('로 채점', '')} · ${st.judge.model} · ${st.judge.effort}`;
  openModal('settingsModal');
}
async function saveSettings() {
  const eff = $('setEffort').querySelector('.on')?.textContent;
  try { S.state.local = await api('/api/settings', { defaultModel: $('setModel').value, defaultEffort: eff }); toast('기본값을 저장했어요'); closeModal('settingsModal'); }
  catch (e) { toast(e.message); }
}
async function saveKeyFromSettings() {
  const key = $('keyInput').value;
  if (!key.trim()) { $('keyInput').focus(); return; }
  $('keySave').disabled = true;
  try {
    const r = await api('/api/key/save', { key });
    toast(r.message); $('keySave').disabled = false;
    await refreshState(); renderSetup(true); openSettings();
  } catch (e) {
    $('keySave').disabled = false; $('keyInput').value = '';
    $('keyErr').textContent = e.message; $('keyErr').classList.remove('hidden');
  }
}
async function checkKey() {
  $('keyCheck').disabled = true;
  try { const r = await api('/api/key/check', {}); S.state.key.checked = r.ok ? 'ok' : 'fail'; S.state.key.message = r.message; } catch (e) { toast(e.message); }
  $('keyCheck').disabled = false;
  await refreshState(); openSettings();
}
function openCase(caseId) {
  const c = S.cases.find((x) => x.caseId === caseId) || S.cases[0];
  $('cSub').textContent = `${c.caseId} · 골든셋을 보며 프롬프트를 맞추는 것은 지양해요`;
  const chips = $('cChips'); chips.innerHTML = '';
  S.cases.forEach((x) => { const b = document.createElement('button'); b.className = 'case-chip'; if (x.caseId === c.caseId) { b.style.background = 'var(--blue)'; b.style.color = '#fff'; } b.textContent = x.label; b.onclick = () => openCase(x.caseId); chips.appendChild(b); });
  const p = c.position; const li = (arr) => `<ul>${(arr || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
  const careers = (c.careers || []).map((k) => `<div class="h">경력 · ${esc(k.company)} ${esc(k.jobTitle || '')} ${esc(k.employmentType || '')} · ${esc(k.period || '')}</div><div class="sm">${esc(k.summaryLine || '')}</div>`).join('');
  const projects = (c.projects || []).map((k) => `<div class="h">프로젝트 · ${esc(k.name)} · ${esc(k.organization || '')} · ${esc(k.teamType || '')}</div><div class="sm">${esc(k.description || '')}</div>`).join('');
  const acts = (c.activities || []).map((k) => `<div class="h">활동 · ${esc(k.name)} · ${esc(k.type || '')}</div><div class="sm">${esc(k.description || '')}</div>`).join('');
  const notes = (c.notes || []).map((k) => `<div class="sm">${esc(k.content || '')}</div>`).join('');
  $('cBody').innerHTML = `
    <div class="case-cols">
      <div class="case-col"><div class="row gap8 center"><span class="chip chip-dark sm">공고</span><b class="h18">${esc(p.positionName || c.job)}</b></div>
        <div class="h">주요 업무</div>${li(p.jobDescription)}<div class="h">자격 요건</div>${li(p.requirements)}<div class="h">우대 사항</div>${li(p.preferredQualifications)}</div>
      <div class="case-col"><div class="row gap8 center"><span class="chip chip-dark sm">지원자</span><b class="h18">${esc(c.who)}</b></div>
        <div class="sm">${esc(c.persona)}</div>${careers}${projects}${acts}${notes ? `<div class="h">메모</div>${notes}` : ''}
        ${c.resume ? `<details><summary>이력서 요약 보기</summary><div class="sm" style="margin-top:8px">${esc(c.resume)}</div></details>` : ''}</div>
    </div>
    <div class="topic-box"><div class="row gap8 center"><span class="chip chip-solid-blue sm">평가 주제</span><b class="h18">${esc(c.category.name)}</b></div>
      <div class="sm">${esc(c.category.definition)}</div>
      ${c.items.map((it, i) => `<div class="it"><b class="sm">요소 ${i + 1} · ${esc(it.name)}</b><div class="sub sm">${esc(it.definition)}</div></div>`).join('')}
      ${c.forbidden?.length ? `<b class="org sm">질문에 쓰면 안 되는 말 · ${c.forbidden.map(esc).join(' · ')}</b>` : ''}</div>`;
  openModal('caseModal');
}

/* ---------------- 화면 전환·연결 ---------------- */
function route(view, push = true) {
  $('view-edit').classList.toggle('hidden', view !== 'edit'); $('view-result').classList.toggle('hidden', view !== 'result');
  document.body.classList.toggle('on-edit', view === 'edit'); document.body.classList.toggle('on-result', view === 'result');
  document.querySelectorAll('.nav-item').forEach((b) => b.classList.toggle('on', b.dataset.view === view));
  if (push) history.pushState({ view }, '', view === 'result' ? '/result' : '/');
  if (view === 'result') { loadResult(); if (SN.mode === 'done') hideSnack(); } else closeDrawer();
  if (view === 'edit' && S.diff) setTimeout(() => S.diff.layout(), 0);
}
function bind() {
  document.addEventListener('click', (e) => {
    const v = e.target.closest('[data-view]'); if (v) { e.preventDefault(); route(v.dataset.view); return; }
    const o = e.target.closest('[data-open]'); if (o) { openModal(o.dataset.open); return; }
    const c = e.target.closest('[data-close]'); if (c) { const m = c.closest('.modal-back'); if (m) m.classList.add('hidden'); }
  });
  document.querySelectorAll('.modal-back').forEach((m) => m.addEventListener('mousedown', (e) => { if (e.target === m && m.id !== 'confirmModal') m.classList.add('hidden'); }));
  window.addEventListener('popstate', () => route(location.pathname === '/result' ? 'result' : 'edit', false));
  window.addEventListener('beforeunload', (e) => { if (isDirty()) { e.preventDefault(); e.returnValue = ''; } });
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); saveAll(); }
    if (e.key === 'Escape' && S.drawerCase && document.querySelectorAll('.modal-back:not(.hidden)').length === 0) closeDrawer();
  });
  $('drClose').onclick = closeDrawer;
  $('saveBtn').onclick = () => saveAll();
  $('revertBtn').onclick = revertStage;
  $('genBtn').onclick = () => openGen();
  $('viewSeg').querySelectorAll('button').forEach((b) => { b.onclick = () => {
    S.side = b.dataset.side === '1'; $('viewSeg').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    $('edCols').classList.toggle('single', !S.side); if (S.diff) S.diff.updateOptions({ renderSideBySide: S.side });
  }; });
  $('railToggle').onclick = () => { UI.panel = !UI.panel; applyPanel(); };
  $('panelClose').onclick = () => { UI.panel = false; applyPanel(); };
  document.querySelectorAll('.rail-item').forEach((b) => { b.onclick = () => {
    const sec = b.dataset.rail;
    if (UI.panel && UI.open[sec]) { UI.open[sec] = false; } else { UI.panel = true; UI.open[sec] = true; }
    applyPanel();
    document.querySelector(`.acc[data-sec="${sec}"]`)?.scrollIntoView({ block: 'nearest' });
  }; });
  document.querySelectorAll('.acc-h').forEach((h) => { h.onclick = () => { const sec = h.closest('.acc').dataset.sec; UI.open[sec] = !UI.open[sec]; applyPanel(); }; });
  $('snack').addEventListener('click', (e) => {
    const b = e.target.closest('[data-sn]'); if (!b) return;
    const a = b.dataset.sn;
    if (a === 'toggle') { SN.expanded = !SN.expanded; if (S.state.running) renderSnackRunning(S.state.running); }
    if (a === 'stop') stopRun();
    if (a === 'stay') { SN.stay = true; clearInterval(SN.timer); SN.timer = null; if (SN.res) renderSnackDone(SN.res); }
    if (a === 'result') goResult();
    if (a === 'close') hideSnack();
  });
  document.querySelectorAll('input[name=scope]').forEach((r) => { r.onchange = () => { S.genScope = r.value; renderGen(); }; });
  $('genModel').onchange = () => fillEfforts($('genEffort').value);
  $('genStart').onclick = startGen;
  $('rerunBtn').onclick = () => { const r = S.result; openGen(r ? { scope: r.scope, cases: r.cases, model: r.model, effort: r.effort } : null); };
  $('openSettings').onclick = openSettings;
  $('setSave').onclick = saveSettings;
  $('keyCheck').onclick = checkKey;
  $('keySave').onclick = saveKeyFromSettings;
  $('keyInput').onkeydown = (e) => { if (e.key === 'Enter') saveKeyFromSettings(); };
  $('keyChange').onclick = () => { $('keyEdit').classList.remove('hidden'); $('keyActs').classList.add('hidden'); $('keyInput').focus(); };
  $('keyCancel').onclick = () => { $('keyEdit').classList.add('hidden'); $('keyActs').classList.remove('hidden'); $('keyInput').value = ''; $('keyErr').classList.add('hidden'); };
  if (window.ResizeObserver) new ResizeObserver(([en]) => { $('edCard').classList.toggle('narrow', en.contentRect.width < 1100); }).observe($('edCard'));
}
async function init() {
  bind();
  try {
    [S.state, S.prompts, S.cases] = await Promise.all([api('/api/state'), api('/api/prompts'), api('/api/cases')]);
  } catch (e) { showBanner(`화면 데이터를 불러오지 못했어요: ${e.message}`, 'red'); return; }
  $('brandTitle').textContent = S.state.title;
  for (const id of Object.keys(S.prompts)) { S.saved[id] = S.prompts[id].mine; S.current[id] = S.prompts[id].mine; }
  $('savedText').textContent = '저장된 프롬프트를 불러왔어요';
  renderTop(); renderBanners(); renderTabs(); renderPanel(); renderCaseChips(); renderCriteria(); applyPanel(); renderSetup(true);
  setupEditor();
  route(location.pathname === '/result' ? 'result' : 'edit', false);
  setInterval(refreshState, 5000);
  if (isRunning()) {
    try { SN.est = (await api(`/api/estimate?cases=${S.state.running.total}`)).seconds; } catch { SN.est = null; }
    startPolling();
  }
}
init();
