# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""잡다 프롬프트 콘테스트 로컬 실행기.

  uv run app.py                 화면 켜기 (http://localhost:8000). 처음이면 개인 브랜치와 API 키부터 챙겨요
  uv run app.py setup           개인 브랜치만 만들기 (/시작에서 사용)
  uv run app.py doctor          실행 환경 점검
  uv run app.py generate        화면 없이 우선 직무 전체 평가(공통 케이스 10개 · 질문 20개) 생성·채점
  uv run app.py generate --cases P01,P03   우선 직무 케이스 골라 생성(케이스당 질문 2개)
  uv run app.py generate --ext  확장 연습(우선 직무 밖 20케이스, 평가 미포함 참고 점수) · --ext --cases P08로 골라서
  uv run app.py status          남은 질문과 최근 결과
  uv run app.py submit          제출: prompts/·runs/ 커밋·푸시 후 PR 열기
"""
from __future__ import annotations

import argparse
import difflib
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import webbrowser

from contest import core, engine, recorder

C = core.CFG


def _p(msg: str = "") -> None:
    print(msg, flush=True)


def _case_ids(arg: str | None, ext: bool = False) -> list[str]:
    if not arg:
        return []
    want = [x.strip().upper() for x in arg.split(",") if x.strip()]
    out = []
    for c in (core.load_ext_cases() if ext else core.load_cases()):
        if c["caseId"].upper() in want or c["positionId"].upper() in want:
            out.append(c["caseId"])
    return out


# ---------------------------------------------------------------------------
def cmd_serve(args) -> int:
    port = args.port or C.get("port", 8000)
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            _p(f"  {port} 포트를 이미 쓰고 있어요. 화면이 이미 켜져 있다면 http://localhost:{port} 를 여세요.")
            _p(f"  다른 포트로 켜려면: uv run app.py --port {port + 1}")
            return 1
    prepare(interactive=sys.stdin.isatty())
    from contest import server
    if not args.no_browser:
        threading_open(f"http://localhost:{port}")
    server.serve(port)
    return 0


def threading_open(url: str) -> None:
    import threading
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()


# ---------------------------------------------------------------------------
# 처음 준비: 개인 브랜치와 API 키
# ---------------------------------------------------------------------------
def _read_clipboard() -> str | None:
    if sys.platform == "darwin":
        cmds = [["pbpaste"]]
    elif os.name == "nt":
        cmds = [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]
    else:
        cmds = [["wl-paste", "-n"], ["xclip", "-o", "-selection", "clipboard"], ["xsel", "-b", "-o"]]
    for c in cmds:
        try:
            r = subprocess.run(c, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return r.stdout
    return None


def prepare(interactive: bool) -> None:
    """처음 실행이면 개인 브랜치를 만들고 API 키를 받는다. 이미 끝났으면 아무 말도 하지 않는다."""
    info = core.git_info()
    if not info.get("repo"):
        return
    need_branch = not (info.get("branch") or "").startswith(C["branch_prefix"])
    need_key = not core.load_api_key()
    if not need_branch and not need_key:
        return
    _p("  처음 실행이라 준비부터 할게요. (이 PC에서 한 번만)")
    core.LOCAL_DIR.mkdir(exist_ok=True)
    if need_branch:
        name = core.suggest_branch_name()
        if not name and interactive:
            name = input("  ? 개인 브랜치  영문 이름을 입력하세요 (예: roy): ").strip()
        if name:
            r = core.ensure_branch(name)
            _p(f"  ✓ 개인 브랜치  {r['branch']} · {r['message']}" if r["ok"] else f"  ✗ 개인 브랜치  {r['message']}")
        else:
            _p("  · 개인 브랜치  화면 맨 위 시작 준비 카드에서 만들어요")
    if need_key:
        if not interactive:
            _p("  · API 키      화면 맨 위 시작 준비 카드에 붙여 넣어요")
            return
        _p("  ? API 키      DM으로 받은 키를 복사한 뒤 Enter를 누르세요 (나중에 화면에서 넣으려면 s)")
        for _ in range(3):
            try:
                ans = getpass.getpass("    > ")
            except (EOFError, KeyboardInterrupt):
                _p("")
                break
            if ans.strip().lower() == "s":
                _p("  · API 키      화면 맨 위 시작 준비 카드에서 넣어요")
                break
            raw = ans.strip() or _read_clipboard()
            if raw is None:
                raw = getpass.getpass("    클립보드를 읽지 못했어요. 키를 붙여 넣고 Enter: ")
            res = core.try_save_key(raw)
            _p(f"  {'✓' if res['ok'] else '✗'} API 키      {res['message']}")
            if res["ok"]:
                break
            _p("    키를 다시 복사한 뒤 Enter를 누르세요 (건너뛰려면 s)")
    _p("")


def cmd_setup(args) -> int:
    """/시작에서 쓴다. 묻지 않고 처리할 수 있는 것만 한다(키는 사람이 화면에서 넣는다)."""
    info = core.git_info()
    if not info.get("repo"):
        _p("  git 저장소가 아니에요. 운영 레포를 git clone 한 폴더에서 실행해 주세요.")
        return 1
    if args.git_name:
        core.git("config", "user.name", args.git_name)
    if args.git_email:
        core.git("config", "user.email", args.git_email)
    info = core.git_info()
    _p(f"  git 사용자   {info.get('user') or '없음'} <{info.get('email') or '없음'}>")
    if not info.get("user") or not info.get("email"):
        _p("    → git 사용자 이름·이메일이 필요해요: uv run app.py setup --git-name '이름' --git-email '메일'")
        return 1
    core.LOCAL_DIR.mkdir(exist_ok=True)
    prefix = C["branch_prefix"]
    if (info.get("branch") or "").startswith(prefix):
        _p(f"  개인 브랜치  {info['branch']}")
    else:
        name = args.name or core.suggest_branch_name()
        if not name:
            _p("  개인 브랜치  이름을 정하지 못했어요 → uv run app.py setup --name 영문이름")
            return 1
        r = core.ensure_branch(name)
        if not r["ok"]:
            _p("  개인 브랜치  " + r["message"])
            return 1
        _p(f"  개인 브랜치  {r['branch']} · {r['message']}")
    last4 = core.key_last4()
    _p(f"  API 키      {'저장돼 있음 · 끝자리 ' + last4 if last4 else '비어 있음 → 화면 맨 위 시작 준비 카드에 붙여 넣으세요'}")
    _p("")
    _p("  다음: uv run app.py 로 화면을 켜세요.")
    return 0


def cmd_doctor(args) -> int:
    info = core.git_info()
    rows = [
        ("Python", sys.version.split()[0], sys.version_info >= (3, 10)),
        ("git 저장소", "예" if info.get("repo") else "아니요", bool(info.get("repo"))),
        ("개인 브랜치", info.get("branch") or "-", (info.get("branch") or "").startswith(C["branch_prefix"])),
        ("git 사용자", f"{info.get('user') or '-'} <{info.get('email') or '-'}>", bool(info.get("user") and info.get("email"))),
        ("원격 저장소", "있음" if info.get("remote") else "없음", bool(info.get("remote"))),
        ("API 키(.env)", "들어 있음" if core.load_api_key() else "비어 있음", bool(core.load_api_key())),
    ]
    v = recorder.verify()
    rows.append(("실행 기록", "정상" if v["ok"] else "; ".join(v["problems"][:2]), v["ok"]))
    try:
        u = recorder.usage()
        rows.append(("남은 질문", f"{u['remaining']} / {u['limit']}", True))
    except recorder.RecordError as e:
        rows.append(("남은 질문", str(e), False))
    if args.ping and core.load_api_key():
        ok, msg = engine.make_client().ping()
        rows.append(("키 연결 확인", msg, ok))
    for k, val, ok in rows:
        _p(f"  {'✓' if ok else '✗'} {k:<10} {val}")
    return 0 if all(r[2] for r in rows) else 1


def _print_progress(run: engine.Run, stop_evt) -> None:
    last = None
    while not stop_evt.is_set():
        s = run.snapshot()
        line = f"  생성 {s['genDone']}/{s['total']} · 채점 {s['judgeDone']}/{s['total']}"
        if line != last:
            _p(line)
            last = line
        time.sleep(2)


def cmd_generate(args) -> int:
    ext = bool(getattr(args, "ext", False))
    ids = _case_ids(args.cases, ext)
    if args.cases and not ids:
        _p("  케이스를 찾지 못했어요. 예: --cases P01,P03" + (" (확장 연습은 P08,X-P17-C2처럼)" if ext else ""))
        return 1
    model = args.model or core.local_settings()["defaultModel"]
    effort = args.effort or core.local_settings()["defaultEffort"]
    err = core.valid_model(model, effort)
    if err:
        _p("  " + err)
        return 1
    if not core.load_api_key():
        _p("  API 키가 없어요. .env 파일에 OPENAI_API_KEY를 직접 넣어 주세요.")
        return 1
    block = core.change_block_message(core.submit_check())
    if block:
        _p("  ✗ " + block)
        return 1
    run = engine.Run("ext" if ext else ("pick" if ids else "all"), ids, model, effort)
    try:
        recorder.preflight(run.questions)
    except recorder.RecordError as e:
        _p("  " + str(e))
        return 1
    what = "확장 연습" if ext else ("우선 직무 전체 평가" if run.scope == "all" else "우선 직무 케이스 골라 실행")
    _p(f"  {what} · 질문 {run.questions}개 생성 · {model} · {effort} · 채점 {C['judge']['model']}")
    import threading
    stop_evt = threading.Event()
    t = threading.Thread(target=_print_progress, args=(run, stop_evt), daemon=True)
    t.start()
    try:
        snap = run.execute()
    except KeyboardInterrupt:
        run.stop_flag.set()
        _p("  멈추는 중이에요. 이미 보낸 요청이 끝날 때까지 기다려 주세요.")
        snap = run.snapshot()
    stop_evt.set()
    if snap["status"] == "error":
        _p("  " + snap["message"])
        return 1
    r = run.result or {}
    _p("")
    _p(f"  결과 파일   {snap.get('result')}")
    if r.get("evalScore") is not None:
        _p(f"  평가 점수   {r['evalScore']} / 100 (우선 직무 전체 평가)")
    elif ext:
        same = r.get("baselineSame")
        _p(f"  참고 점수   {r.get('questionAvg')} / 100" + (f" · 같은 케이스 v9.0.0 {same}" if same is not None else "") + " · 평가 점수·공식 점수와 무관")
        g = r.get("gapCheck") or {}
        if g.get("core") and g.get("ext"):
            _p(f"  처음 보는 케이스 점검  우선 직무 {g['core']['delta']:+} · 확장 연습 {g['ext']['delta']:+}")
        msg = {"core_only": "우선 직무에서만 크게 올랐어요. 공개 케이스에 맞춰졌는지 확인해 보세요.",
               "similar": "두 묶음에서 비슷하게 올랐어요.",
               "no_core": "우선 직무 전체 평가를 한 번 돌리면 비교할 수 있어요."}.get(g.get("verdict"))
        if msg:
            _p("  " + msg + (" (우선 직무 전체 평가 뒤에 프롬프트가 바뀌었어요)" if g.get("samePrompt") is False else ""))
    else:
        _p(f"  질문 점수 평균 {r.get('questionAvg')} · {r.get('evalNote') or ''}")
    for d in C["rubric_display"]:
        _p(f"  {d['id']} {d['name']:<14} {r.get('dimAvg', {}).get(d['id'])} / {d['weight']}")
    if r.get("failCounts"):
        _p("  실패 유형   " + ", ".join(f"{k}×{v}" for k, v in r["failCounts"].items()))
    if r.get("ruleFails"):
        _p("  규칙 진단   " + ", ".join(f"{k}×{v}" for k, v in r["ruleFails"].items()) + " (A00과 질문 수·중복 오류만 케이스 0점)")
    ps = recorder.wait_push(60)
    _p(f"  기록        {ps.get('message')}")
    u = recorder.usage()
    _p(f"  남은 질문   {u['remaining']} / {u['limit']}")
    return 0


def cmd_status(args) -> int:
    u = recorder.usage()
    _p(f"  남은 질문   {u['remaining']} / {u['limit']} (실행 {u['runs']}번)")
    lst = engine.list_results()
    if lst:
        r = lst[-1]
        if r["evalScore"] is not None:
            score = f"우선 직무 전체 평가 {r['evalScore']}"
        elif r.get("set") == "ext":
            score = f"확장 연습 참고 점수 {r['questionAvg']}"
        else:
            score = f"질문 평균 {r['questionAvg']}"
        _p(f"  최근 실행   {r['runId']} · {score}")
    n = recorder.ahead_count()
    if n:
        _p(f"  아직 못 올린 기록 커밋 {n}개 → 화면을 켜거나 다음 실행 때 올라가요")
    return 0


def cmd_verify(args) -> int:
    v = recorder.verify()
    _p(f"  기록 줄 수  {v['rows']}")
    _p("  기록 상태   " + ("정상" if v["ok"] else "문제 있음"))
    for p in v["problems"]:
        _p("   - " + p)
    dirty = recorder.runs_dirty()
    if dirty:
        _p("  runs/에 커밋되지 않은 변경: " + ", ".join(dirty[:5]))
    _p(f"  코드 지문   {core.code_hash()[:16]}")
    return 0 if v["ok"] and not dirty else 1


def _diff_counts(a: str, b: str) -> tuple[int, int]:
    add = rem = 0
    for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            rem += 1
    return add, rem


def _compare_url(remote: str, branch: str) -> str | None:
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", remote or "")
    return f"https://github.com/{m.group(1)}/compare/main...{branch}?expand=1" if m else None


def cmd_submit(args) -> int:
    info = core.git_info()
    branch = info.get("branch") or ""
    if not branch.startswith(C["branch_prefix"]):
        _p(f"  개인 브랜치({C['branch_prefix']}영문이름)에서만 제출할 수 있어요.")
        return 1
    v = recorder.verify()
    if not v["ok"] or recorder.runs_dirty():
        _p("  실행 기록에 문제가 있어 제출할 수 없어요. 운영자에게 알려 주세요.")
        for p in v["problems"]:
            _p("   - " + p)
        return 1
    mine, base = core.read_prompt("assignments"), core.read_prompt("assignments", baseline=True)
    add, rem = _diff_counts(base, mine)
    sc = core.submit_check(mine)
    pct = min(sc["ratio"], 1) * 100
    block = core.change_block_message(sc, "제출")
    if block:
        _p("  ✗ " + block)
        return 1
    _p(f"  ③ 변경      v9.0.0 대비 +{add}줄 −{rem}줄 · 바뀐 분량 {pct:.1f}%{' 이상' if sc['ratio'] >= 1 else ''}")
    fmt = core.format_changes("assignments", mine)
    if fmt:
        _p("  ⚠ 입력·출력 형식 영역이 바뀌었어요: " + ", ".join(fmt))
    u = recorder.usage()
    lst = engine.list_results()
    full = [r for r in lst if r.get("evalScore") is not None]
    _p(f"  사용한 질문  {u['used']} / {u['limit']}")
    if full:
        _p(f"  최근 평가 점수 {full[-1]['evalScore']} (연습 기준, 공식 점수는 운영자 PC에서 다시 매겨요)")
    core.git("add", "--", "prompts", "runs")
    r = core.git("commit", "--no-verify", "-m", f"제출: {branch}", "--", "prompts", "runs")
    if r.returncode == 0:
        _p("  커밋        prompts/·runs/ 변경을 커밋했어요")
    else:
        _p("  커밋        새로 커밋할 변경이 없어요")
    ps = recorder.push_now()
    _p(f"  푸시        {ps.get('message')}")
    if ps.get("state") != "pushed":
        return 1
    # PR
    title = f"[제출] {branch.split('/', 1)[1]}"
    body = (f"- 브랜치: {branch}\n- 사용한 질문: {u['used']} / {u['limit']}\n- ③ 변경: v9.0.0 대비 +{add}줄 −{rem}줄 · 바뀐 분량 {pct:.1f}%\n"
            f"- 최근 연습 평가 점수: {full[-1]['evalScore'] if full else '없음'}\n- 코드 지문: {core.code_hash()[:16]}\n\n"
            "마감 시점 이 PR의 마지막 커밋이 제출본입니다. 머지하지 않습니다.")
    if shutil.which("gh") and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0:
        ex = subprocess.run(["gh", "pr", "view", branch, "--json", "url", "-q", ".url"], cwd=core.ROOT, capture_output=True, text=True)
        if ex.returncode == 0 and ex.stdout.strip():
            _p(f"  PR          이미 열려 있어요: {ex.stdout.strip()} (방금 푸시한 커밋이 반영됐어요)")
            return 0
        cr = subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body],
                            cwd=core.ROOT, capture_output=True, text=True)
        if cr.returncode == 0:
            _p(f"  PR          열었어요: {cr.stdout.strip().splitlines()[-1]}")
            return 0
        _p("  PR          gh로 열지 못했어요: " + cr.stderr.strip()[-200:])
    url = _compare_url(info.get("remote") or "", branch)
    _p(f"  PR          브라우저에서 열어 주세요: {url}" if url else "  PR          GitHub에서 main ← " + branch + " PR을 열어 주세요.")
    _p(f"  PR 제목     {title}")
    return 0


# ---------------------------------------------------------------------------
def cmd_baseline(args) -> int:
    """운영자용: v9.0.0 프롬프트로 같은 조건을 n번 생성·채점해 기준 점수를 만든다. 참가자 기록에는 남기지 않는다."""
    if not args.operator:
        _p("  운영자용 명령이에요.")
        return 1
    if not core.load_api_key():
        _p("  API 키가 없어요.")
        return 1
    model = args.model or C["generation"]["default_model"]
    effort = args.effort or C["generation"]["default_effort"]
    prompt = core.read_prompt("assignments", baseline=True)
    ext = bool(getattr(args, "ext", False))
    runs = []
    for i in range(args.runs):
        _p(f"  기준선 {i + 1}/{args.runs} 생성 중 …" + (" (확장 연습)" if ext else ""))
        run = engine.Run("ext" if ext else "all", [], model, effort, prompt_text=prompt, record=False, label=f"baseline{'-ext' if ext else ''}-{i + 1}")
        run.execute()
        r = run.result
        qs = {it["itemId"]: it["question"] for c in r["items"] for it in c["items"] if it.get("question")}
        # 확장 연습은 평가 점수가 없으니 질문 평균을 점수로 쓴다(생성·채점 실패가 있으면 빼고 다시 돌린다).
        sc = r["evalScore"] if not ext else (r["questionAvg"] if not any(c.get("genError") or c.get("judgeError") for c in r["items"]) else None)
        runs.append({"score": sc, "questionAvg": r["questionAvg"], "dimAvg": r["dimAvg"], "durationSec": r["durationSec"],
                     "failCounts": r["failCounts"], "ruleFails": r["ruleFails"], "questions": qs, "detail": r})
        _p(f"    {'참고 점수' if ext else '평가 점수'} {sc} · {r['durationSec']}초")
    scores = [x["score"] for x in runs if x["score"] is not None]
    if not scores:
        _p("  평가 점수를 하나도 얻지 못했어요. 오류를 확인해 주세요.")
        return 1
    workers = C.get("workers", 5)
    batches = max(1, -(-len(core.load_ext_cases() if ext else core.load_cases()) // workers))
    case_vals: dict[str, list[float]] = {}
    for x in runs:
        for c in x["detail"]["items"]:
            if c.get("avg") is not None:
                case_vals.setdefault(c["caseId"], []).append(c["avg"])
    out = {
        "createdAt": recorder.now_iso(),
        "model": model, "effort": effort, "judge": C["judge"],
        "mean": round(sum(scores) / len(scores), 1),
        "spread": round(max(scores) - min(scores), 1),
        "scores": scores,
        "secPerCase": round(sum(x["durationSec"] for x in runs) / len(runs) / batches, 1),
        "caseAvg": {k: round(sum(v) / len(v), 1) for k, v in case_vals.items()},
        "runs": runs,
    }
    name = "extension.json" if ext else "results.json"
    if ext:
        out["source"] = "앱 경로(Responses API)로 v9.0.0을 확장 연습 케이스 20개에 생성·채점. 확장 연습 화면의 같은 케이스 비교용(평가 점수·패널티와 무관)."
    (core.BASELINE_DIR / name).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if ext:
        _p(f"  확장 연습 기준 {out['mean']} · 흔들림 폭 {out['spread']} (참고 점수 비교용)")
    else:
        _p(f"  연습 기준 점수 {out['mean']} · 흔들림 폭 {out['spread']} (패널티 기준은 baseline/official.json)")
    _p(f"  baseline/{name} 에 저장했어요. main에 커밋해 참가자에게 배포하세요.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uv run app.py", description="잡다 프롬프트 콘테스트")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않기")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("setup", help="처음 한 번: .env 준비, 개인 브랜치 만들기")
    p.add_argument("--name", help="영문 이름 (브랜치 contest/이름). 없으면 GitHub 로그인 이름을 써요")
    p.add_argument("--git-name")
    p.add_argument("--git-email")
    p = sub.add_parser("doctor", help="실행 환경 점검")
    p.add_argument("--ping", action="store_true", help="키가 살아 있는지 확인(비용 없음)")
    p = sub.add_parser("generate", help="화면 없이 생성·채점 (기본: 우선 직무 전체 평가)")
    p.add_argument("--cases", help="케이스 골라 실행. 예: P01,P03")
    p.add_argument("--ext", action="store_true", help="확장 연습(참고 점수). --cases와 함께 쓰면 그 케이스만. 예: --ext --cases P08,X-P17-C2")
    p.add_argument("--model")
    p.add_argument("--effort")
    sub.add_parser("status", help="남은 질문과 최근 결과")
    sub.add_parser("verify", help="실행 기록 점검")
    sub.add_parser("submit", help="제출: 커밋·푸시·PR")
    p = sub.add_parser("baseline", help=argparse.SUPPRESS)
    p.add_argument("--operator", action="store_true")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--ext", action="store_true", help="확장 연습 기준(baseline/extension.json)")
    args = ap.parse_args(argv)
    fn = {"setup": cmd_setup, "doctor": cmd_doctor, "generate": cmd_generate, "status": cmd_status,
          "verify": cmd_verify, "submit": cmd_submit, "baseline": cmd_baseline}.get(args.cmd, cmd_serve)
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
