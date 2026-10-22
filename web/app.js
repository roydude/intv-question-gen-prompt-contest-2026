/* 잡다 프롬프트 콘테스트 로컬 화면 */
const MONACO = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min';
const S = {
  state: null, prompts: null, cases: [], stage: 'assignments', saved: {}, current: {},
  side: true, diff: null, models: {}, fallback: false, decos: [], result: null,
  genScope: 'all', genPick: new Set(), poll: null, lastSavedAt: null, updTimer: null,
};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new Error((j && j.error) || `요청이 실패했어요 (${r.status})`);
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
  const c = chainById(S.stage);
  $('edTitle').textContent = `${c.no} ${c.name} 프롬프트`;
  if (S.fallback) { $('fallbackEditor').value = S.current[S.stage]; }
  else if (S.diff) { S.diff.setModel({ original: S.models[S.stage].orig, modified: S.models[S.stage].mod }); }
  applyReadOnly();
  updateEditorMeta();
}
function applyReadOnly() {
  const ro = isRunning();
  if (S.diff) S.diff.getModifiedEditor().updateOptions({ readOnly: ro });
  if (S.fallback) $('fallbackEditor').readOnly = ro;
}
function scheduleUpdate() { clearTimeout(S.updTimer); S.updTimer = setTimeout(updateEditorMeta, 250); }
function updateEditorMeta() {
  const id = S.stage; const base = S.prompts[id].base; const mine = S.current[id];
  const lines = (t) => t.replace(/\n$/, '').split('\n').length;
  const nb = lines(base); const nm = lines(mine);
  $('edLines').textContent = `${nb}줄 → ${nm}줄`;
  const d = diffCounts(base, mine);
  $('edDiff').textContent = d.add || d.rem ? `v9.0.0 대비 +${d.add}줄 −${d.rem}줄` : 'v9.0.0과 같아요';
  const fc = formatChanged(id, mine);
  const fs = $('fmtStatus');
  fs.textContent = fc.length ? `⚠ 형식 영역이 바뀌었어요: ${fc.join(', ')}` : '✓ 입력·출력 형식 영역 변경 없음';
  fs.className = `b6 sm ${fc.length ? 'org' : 'green'}`;
  if (S.diff && window.monaco) {
    const ed = S.diff.getModifiedEditor();
    S.decos = ed.deltaDecorations(S.decos, formatInfo(mine).ranges.map(([a, b]) => ({
      range: new monaco.Range(a, 1, Math.max(a, b), 1),
      options: { isWholeLine: true, className: 'fmt-zone', glyphMarginClassName: 'fmt-glyph', hoverMessage: { value: '형식 영역 · 키 이름과 구조를 바꾸면 생성이 실패해요' } },
    })));
  }
  renderDirty();
}
function renderDirty() {
  $('dirtyChip').classList.toggle('hidden', !isDirty());
  renderSteps();
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
  toast('저장했어요');
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

/* ---------------- 상단·배너·체인·오른쪽 ---------------- */
function renderTop() {
  const st = S.state; const u = st.usage;
  $('deadlineChip').textContent = st.deadline;
  $('remainChip').textContent = `남은 질문 ${u.remaining}/${u.limit}`;
  const rc = $('runChip');
  if (isRunning()) {
    const r = st.running; rc.textContent = `생성 중 ${r.judgeDone}/${r.total}`; rc.classList.remove('hidden');
  } else rc.classList.add('hidden');
  $('branchChip').textContent = st.git.branch ? `브랜치 ${st.git.branch}` : 'git 저장소 아님';
  applyReadOnly();
}
const bannerKeys = new Set();
function showBanner(text, tone, key, actionLabel, action) {
  if (key && bannerKeys.has(key)) return;
  const b = document.createElement('div'); b.className = `banner ${tone === 'red' ? 'red' : ''}`; b.textContent = text;
  if (key) { b.dataset.key = key; bannerKeys.add(key); }
  if (actionLabel) { const btn = document.createElement('button'); btn.className = 'btn sm'; btn.style.marginLeft = '12px'; btn.textContent = actionLabel; btn.onclick = action; b.appendChild(btn); }
  $('banners').appendChild(b);
}
function renderBanners() {
  document.querySelectorAll('.banner[data-dyn]').forEach((el) => { bannerKeys.delete(el.dataset.key); el.remove(); });
  const st = S.state; const add = (text, tone, key, a, f) => { showBanner(text, tone, key, a, f); const el = document.querySelector(`.banner[data-key="${key}"]`); if (el) el.dataset.dyn = '1'; };
  if (!st.git.repo) add('git 저장소가 아니에요. 운영 레포를 클론한 폴더에서 실행해 주세요.', 'red', 'repo');
  else if (!st.branchOk) add(`개인 브랜치가 아니에요(현재 ${st.git.branch}). Claude Code에서 /시작으로 ${st.branchPrefix}영문이름 브랜치를 만든 뒤 생성할 수 있어요.`, 'org', 'branch');
  if (!st.key.present) add('API 키가 비어 있어요. 레포 폴더의 .env 파일에 OPENAI_API_KEY를 직접 넣은 뒤 화면을 새로 고쳐 주세요.', 'org', 'key');
  if (st.usage.error) add(st.usage.error, 'red', 'ledger');
  if (st.push && st.push.state === 'failed') add('실행 기록을 개인 브랜치에 올리지 못했어요. 다음 실행 때 다시 올려요.', 'org', 'push', '지금 다시 올리기', async () => { const r = await api('/api/push', {}); toast(r.message); refreshState(); });
}
function renderSteps() {
  const box = $('steps'); box.innerHTML = '';
  S.state.chain.forEach((c, i) => {
    if (i) { const a = document.createElement('span'); a.className = 'arrow'; a.textContent = '→'; box.appendChild(a); }
    const on = c.id === S.stage;
    const b = document.createElement('button'); b.className = `step ${on ? 'on' : ''}`;
    const state = c.scored ? (on ? '편집 중 · 채점 대상' : '채점 대상') : c.state;
    b.innerHTML = `<span class="t">${esc(c.no)} ${esc(c.name)} <span class="chip xs ${on ? 'chip-solid-blue' : 'chip-white'}">${esc(state)}</span></span>
      <span class="d">${esc(c.desc)}</span>${isDirty(c.id) ? '<span class="dirty">● 저장하지 않은 변경</span>' : ''}`;
    b.onclick = () => { S.stage = c.id; setStageModel(); renderSteps(); };
    box.appendChild(b);
  });
}
function renderSide() {
  const st = S.state; const u = st.usage; const r = st.latest;
  const body = $('lastBody');
  if (!r) { $('lastMeta').textContent = ''; body.innerHTML = '<p class="sub sm">아직 실행한 적이 없어요. 아래 질문 생성으로 첫 결과를 만들어 보세요.</p>'; }
  else {
    $('lastMeta').textContent = `${fmtTime(r.finishedAt)} · 질문 ${r.cases * st.qpc}개`;
    const score = r.evalScore != null ? `<b class="h20">${r.evalScore}</b>` : `<span class="sub sm">질문 평균 ${r.questionAvg ?? '-'}</span>`;
    const base = st.baseline.ready ? `${st.baseline.mean}` : '산출 예정';
    const rf = Object.values(r.ruleFails || {}).reduce((a, b) => a + b, 0);
    const top = Object.entries(r.failCounts || {})[0];
    body.innerHTML = `
      <div class="kv"><span class="sub">평가 점수</span>${score}</div>
      <div class="kv"><span class="sub">기준 점수 (v9.0.0)</span><span class="${st.baseline.ready ? 'b6' : 'sub'}">${esc(base)}</span></div>
      <div class="divider"></div>
      ${st.rubric.map((d) => `<div class="kv"><span class="sub">${d.id} ${esc(d.name)}</span><span class="v">${r.dimAvg?.[d.id] ?? '-'} / ${d.weight}</span></div>`).join('')}
      <div class="kv"><span class="sub">규칙 진단</span><span class="sub sm b5">위반 ${rf}건 · 점수 미반영</span></div>
      <div class="kv"><span class="sub">많이 걸린 유형</span><span class="org sm b6">${top ? `${top[0]} ${esc(failName(top[0]))} ×${top[1]}` : '없음'}</span></div>
      <button class="btn full" data-view="result">결과 보기</button>`;
  }
  $('remainBig').textContent = `${u.remaining} / ${u.limit}`;
  $('remainBar').style.width = `${Math.min(100, ((u.used || 0) / u.limit) * 100)}%`;
  $('remainText').textContent = `사이클 ${Math.floor(u.remaining / (10 * st.qpc))}번 남음 · 기록은 runs/에 남아요`;
}
function renderCaseChips() {
  const box = $('caseChips'); box.innerHTML = '';
  S.cases.forEach((c) => { const b = document.createElement('button'); b.className = 'case-chip'; b.textContent = c.label; b.onclick = () => openCase(c.caseId); box.appendChild(b); });
}
async function refreshState() {
  try { S.state = await api('/api/state'); } catch { return; }
  renderTop(); renderSide(); renderBanners();
  if (isRunning() && !S.poll) startPolling(false);
}

/* ---------------- 생성 창 ---------------- */
function openGen(prefill) {
  if (isRunning()) { openModal('progModal'); return; }
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
  else if (!st.branchOk) err = `개인 브랜치(${st.branchPrefix}영문이름)에서만 생성할 수 있어요. Claude Code에서 /시작을 먼저 해 주세요.`;
  else if (!st.key.present) err = 'API 키가 비어 있어요. .env 파일에 OPENAI_API_KEY를 넣어 주세요.';
  else if (n === 0) err = '실행할 케이스를 하나 이상 골라 주세요.';
  else if (after < 0) err = `남은 질문이 ${st.usage.remaining}개라 ${q}개를 만들 수 없어요.`;
  $('genStart').disabled = !!err;
  $('genErr').textContent = err; $('genErr').classList.toggle('hidden', !err);
  $('sumTime').textContent = '…';
  try {
    const e = await api(`/api/estimate?cases=${n || 1}`);
    $('sumTime').textContent = e.seconds ? `약 ${Math.max(1, Math.round(e.seconds / 60))}분` : '기준선 실측 후 표시';
  } catch { $('sumTime').textContent = '기준선 실측 후 표시'; }
}
async function startGen() {
  if (isDirty() && !(await saveAll())) return;
  const m = $('genModel').value; const model = m === '__custom' ? $('genCustomModel').value.trim() : m;
  const body = { scope: S.genScope, cases: [...S.genPick], model, effort: $('genEffort').value };
  $('genStart').disabled = true;
  try {
    const snap = await api('/api/run', body);
    closeModal('genModal');
    S.state.running = snap; renderTop();
    renderProgress(snap); openModal('progModal'); startPolling(true);
  } catch (e) {
    $('genErr').textContent = e.message; $('genErr').classList.remove('hidden'); $('genStart').disabled = false;
  }
}

/* ---------------- 진행 ---------------- */
function startPolling(autoNavigate) {
  stopPolling();
  S.autoNav = autoNavigate;
  S.poll = setInterval(async () => {
    let snap; try { snap = await api('/api/run/current'); } catch { return; }
    if (!snap) return;
    S.state.running = snap; renderTop(); renderProgress(snap);
    if (snap.status !== 'running') {
      stopPolling(); await refreshState();
      if (snap.status === 'done' || snap.status === 'stopped') {
        if (S.autoNav || !$('progModal').classList.contains('hidden')) {
          setTimeout(() => { closeModal('progModal'); route('result'); }, 1200);
        } else toast('생성이 끝났어요. 결과 화면에서 확인하세요.');
      }
    }
  }, 1200);
}
function stopPolling() { if (S.poll) clearInterval(S.poll); S.poll = null; }
function renderProgress(r) {
  const titles = { running: '질문 생성 중', done: '생성 완료', stopped: '중지됨', error: '생성하지 못했어요' };
  const badges = { running: ['진행 중', 'chip-blue'], done: ['완료', 'chip-green'], stopped: ['중지', 'chip-gray'], error: ['실패', 'chip-red'] };
  $('pTitle').textContent = titles[r.status] || '질문 생성';
  const [bt, bc] = badges[r.status] || ['', '']; $('pBadge').textContent = bt; $('pBadge').className = `chip sm ${bc}`;
  $('pMeta').textContent = `${r.startedAt ? `${hm(r.startedAt)} 시작 · ` : ''}${r.scope === 'all' ? '한 사이클' : `케이스 ${r.total}개`} · ${r.model} · ${r.effort || ''}`;
  const steps = [
    ['① 질문 생성', `${r.genDone} / ${r.total}${r.genDone === r.total ? ' 완료' : ''}`, r.genDone === r.total],
    ['② 규칙 검사', r.checkDone === r.total ? '16개 진단 완료' : `${r.checkDone} / ${r.total}`, r.checkDone === r.total],
    ['③ AI 채점', `${r.judgeDone} / ${r.total}${r.judgeDone === r.total ? ' 완료' : ' 진행 중'}`, r.judgeDone === r.total],
  ];
  const firstOpen = steps.findIndex((s) => !s[2]);
  $('pSteps').innerHTML = steps.map((s, i) => `<div class="pstep ${s[2] ? 'done' : (i === firstOpen && r.status === 'running' ? 'on' : '')}"><b>${s[2] ? '✓ ' : ''}${s[0]}</b><div class="s">${s[1]}</div></div>`).join('');
  $('pBar').style.width = `${r.total ? ((r.genDone + r.judgeDone) / (2 * r.total)) * 100 : 0}%`;
  const label = (c) => {
    if (c.gen === 'skip') return ['건너뜀', 'chip-gray'];
    if (c.gen === 'wait') return ['대기', 'chip-gray'];
    if (c.gen === 'running') return ['생성 중', 'chip-blue'];
    if (c.gen === 'error') return ['생성 실패', 'chip-red'];
    if (c.judge === 'running') return ['채점 중', 'chip-blue'];
    if (c.judge === 'done') return [`채점 완료 ${S.state.qpc}/${S.state.qpc}`, 'chip-green'];
    if (c.judge === 'error') return ['채점 실패', 'chip-red'];
    if (c.judge === 'skip') return ['출력 오류', 'chip-orange'];
    return ['채점 대기', 'chip-gray'];
  };
  $('pList').innerHTML = r.cases.map((c) => { const [t, k] = label(c); return `<div class="pr"><div class="row gap8 center"><b class="sm">${esc(c.label)}</b><span class="muted sm">${esc(c.who)}</span></div><span class="chip sm ${k}">${t}</span></div>`; }).join('');
  $('pErr').textContent = r.message || ''; $('pErr').classList.toggle('hidden', !(r.status === 'error' || r.message));
  $('pStop').disabled = r.status !== 'running';
  $('pClose').textContent = r.status === 'done' || r.status === 'stopped' ? '결과 보기' : '창 닫기';
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
  const st = S.state; const n = r.cases.length;
  $('rScope').textContent = r.scope === 'all' ? `한 사이클 · 질문 ${n * st.qpc}개` : `케이스 ${n}개 · 질문 ${n * st.qpc}개`;
  $('rMeta').textContent = `${fmtTime(r.finishedAt)} · ${r.model} · ${r.effort || ''} · 저장 커밋 ${r.head || '-'}${r.status === 'stopped' ? ' · 중지됨' : ''}`;
  $('rFile').textContent = `runs/${r.runId}.json에 기록됨`;
  const ps = st.push || {}; const pmap = { pushed: ['개인 브랜치에 올라감', 'chip-green'], failed: ['푸시 실패 · 다음 실행 때 다시 올려요', 'chip-orange'], pushing: ['올리는 중', 'chip-blue'], 'no-remote': ['원격 없음 · 커밋만 함', 'chip-gray'] };
  const pp = pmap[ps.state]; $('rPush').classList.toggle('hidden', !pp); if (pp) { $('rPush').textContent = pp[0]; $('rPush').className = `chip sm ${pp[1]}`; }
  $('rScore').textContent = r.evalScore ?? '–';
  $('rScore').className = `h44 ${scoreClass(r.evalScore)}`;
  $('rBase').textContent = st.baseline.ready ? `${st.baseline.mean} · 패널티 기준 ${st.baseline.threshold}` : '산출 예정';
  $('rScoreNote').textContent = r.evalNote || '질문 20개 AI 채점 평균이에요. 기준 점수는 v9.0.0을 3회 생성·채점한 평균이고, 기준에서 흔들림 폭을 뺀 값보다 낮으면 패널티가 있어요.';
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
  $('rRules').innerHTML = `<b class="sm">규칙 진단 16개</b><span class="chip chip-gray sm">위반 ${rfTotal}건 · 점수 미반영</span>
    ${rf.map(([k, v]) => `<span class="chip chip-orange sm">${k} ${esc(ruleName(k))} ×${v}</span>`).join('')}
    <button class="link" data-open="criteriaModal">기준 보기 →</button>`;
  const head = '<div class="tr head"><span>케이스</span><span>지원자</span><span>평가 주제</span><span>점수</span><span>실패 유형</span><span>규칙 진단</span><span></span></div>';
  $('rTable').innerHTML = head + r.items.map((c, i) => caseRow(c, i === 0)).join('');
  $('rTable').querySelectorAll('[data-toggle]').forEach((b) => { b.onclick = () => toggleRow(b.dataset.toggle); });
  $('rTable').querySelectorAll('[data-case]').forEach((b) => { b.onclick = () => openCase(b.dataset.case); });
  $('rTable').querySelectorAll('[data-raw]').forEach((b) => { b.onclick = () => { const el = $(`raw-${b.dataset.raw}`); el.classList.toggle('hidden'); }; });
}
function caseRow(c, open) {
  const codes = {}; c.items.forEach((it) => (it.failCodes || []).forEach((k) => { codes[k] = (codes[k] || 0) + 1; }));
  const ct = Object.entries(codes).map(([k, v]) => `${k} ×${v}`).join(' · ') || '없음';
  const ran = !!(c.outputText || c.genError);
  const rules = !ran || c.genError ? '-' : (c.checks.failed?.length ? c.checks.failed.map((k) => `${k} ×1`).join(' · ') : '위반 0');
  const avg = c.genError ? '실패' : (c.avg ?? '-');
  return `<div class="tr ${open ? 'open' : ''}" id="row-${c.caseId}">
    <b>${esc(c.label)}</b><span class="sub">${esc(c.who)}</span><span>${esc(c.category)}</span>
    <b class="${scoreClass(c.avg)}">${avg}</b><span class="sm b5 ${ct === '없음' || !ran ? 'muted' : 'org'}">${ran ? ct : '-'}</span>
    <span class="sm b5 ${rules === '위반 0' ? 'muted' : 'org'}">${rules}</span>
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
    <span class="lbl blue">이번 질문</span><div class="q">${esc(it.question || '(질문 없음)')}</div>
    <span class="lbl muted">v9.0.0 질문</span><div class="sub sm">${esc(it.baselineQuestion || '기준선 생성 후 표시')}</div>
    ${it.points ? `<div class="wrap" style="margin-top:0">${chips}${fail}</div>` : ''}
    ${reasons.J2 ? `<div class="reason">근거 · ${esc(reasons.J2)}${others ? `<ul>${others}</ul>` : ''}</div>` : ''}
    <div class="case-foot">${ruleLine}<button class="link-btn" data-case="${c.caseId}">공고·지원자 정보 보기 →</button></div></div>`;
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
  if (!k.present) { chip = ['키 없음', 'chip-red']; msg = '.env 파일에 키가 비어 있어요'; cls = 'bad'; }
  else if (k.checked === 'ok') { chip = ['연결됨', 'chip-solid-blue']; msg = '.env 파일에서 읽었어요'; cls = 'ok'; }
  else if (k.checked === 'fail') { chip = ['확인 실패', 'chip-red']; msg = k.message || '키를 확인해 주세요'; cls = 'bad'; }
  else { chip = ['읽음', 'chip-white']; msg = '.env 파일에서 읽었어요 · 다시 확인으로 연결을 점검해요'; cls = ''; }
  $('keyChip').textContent = chip[0]; $('keyChip').className = `chip sm ${chip[1]}`; $('keyMsg').textContent = msg; box.className = `key-box ${cls}`;
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
  document.querySelectorAll('.nav-item').forEach((b) => b.classList.toggle('on', b.dataset.view === view));
  if (push) history.pushState({ view }, '', view === 'result' ? '/result' : '/');
  if (view === 'result') loadResult();
  if (view === 'edit' && S.diff) setTimeout(() => S.diff.layout(), 0);
}
function bind() {
  document.addEventListener('click', (e) => {
    const v = e.target.closest('[data-view]'); if (v) { e.preventDefault(); route(v.dataset.view); return; }
    const o = e.target.closest('[data-open]'); if (o) { openModal(o.dataset.open); return; }
    const c = e.target.closest('[data-close]'); if (c) { const m = c.closest('.modal-back'); if (m) m.classList.add('hidden'); if (c.id === 'pClose' && c.textContent === '결과 보기') route('result'); }
  });
  document.querySelectorAll('.modal-back').forEach((m) => m.addEventListener('mousedown', (e) => { if (e.target === m && m.id !== 'confirmModal') m.classList.add('hidden'); }));
  window.addEventListener('popstate', () => route(location.pathname === '/result' ? 'result' : 'edit', false));
  window.addEventListener('beforeunload', (e) => { if (isDirty()) { e.preventDefault(); e.returnValue = ''; } });
  document.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); if (!isRunning()) saveAll(); } });
  $('saveBtn').onclick = () => { if (isRunning()) toast('생성 중에는 저장할 수 없어요'); else saveAll(); };
  $('revertBtn').onclick = revertStage;
  $('viewSeg').querySelectorAll('button').forEach((b) => { b.onclick = () => {
    S.side = b.dataset.side === '1'; $('viewSeg').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    $('edCols').classList.toggle('single', !S.side); if (S.diff) S.diff.updateOptions({ renderSideBySide: S.side });
  }; });
  $('openGen').onclick = () => openGen();
  $('runChip').onclick = () => { renderProgress(S.state.running); openModal('progModal'); };
  document.querySelectorAll('input[name=scope]').forEach((r) => { r.onchange = () => { S.genScope = r.value; renderGen(); }; });
  $('genModel').onchange = () => fillEfforts($('genEffort').value);
  $('genStart').onclick = startGen;
  $('pStop').onclick = stopRun;
  $('rerunBtn').onclick = () => { const r = S.result; openGen(r ? { scope: r.scope, cases: r.cases, model: r.model, effort: r.effort } : null); };
  $('openSettings').onclick = openSettings;
  $('setSave').onclick = saveSettings;
  $('keyCheck').onclick = checkKey;
  $('critSeg').querySelectorAll('button').forEach((b) => { b.onclick = () => {
    $('critSeg').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    $(b.dataset.anchor).scrollIntoView({ behavior: 'smooth', block: 'start' });
  }; });
}
async function init() {
  bind();
  try {
    [S.state, S.prompts, S.cases] = await Promise.all([api('/api/state'), api('/api/prompts'), api('/api/cases')]);
  } catch (e) { showBanner(`화면 데이터를 불러오지 못했어요: ${e.message}`, 'red'); return; }
  $('brandTitle').textContent = S.state.title;
  for (const id of Object.keys(S.prompts)) { S.saved[id] = S.prompts[id].mine; S.current[id] = S.prompts[id].mine; }
  $('savedText').textContent = '저장된 프롬프트를 불러왔어요';
  renderTop(); renderBanners(); renderSteps(); renderSide(); renderCaseChips(); renderCriteria();
  setupEditor();
  route(location.pathname === '/result' ? 'result' : 'edit', false);
  setInterval(refreshState, 5000);
  if (isRunning()) startPolling(false);
}
init();
