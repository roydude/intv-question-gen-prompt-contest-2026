# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""잡다 프롬프트 콘테스트 로컬 실행기.

  uv run app.py                 화면 켜기 (http://localhost:8000)
  uv run app.py setup --name gaon   처음 한 번: .env 준비, 개인 브랜치 contest/gaon 만들기
  uv run app.py doctor          실행 환경 점검
  uv run app.py generate        화면 없이 한 사이클(질문 20개) 생성·채점
  uv run app.py generate --cases P01,P03   케이스 골라 생성(케이스당 질문 2개)
  uv run app.py status          남은 질문과 최근 결과
  uv run app.py submit          제출: prompts/·runs/ 커밋·푸시 후 PR 열기
"""
from __future__ import annotations

import argparse
import difflib
import json
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


def _case_ids(arg: str | None) -> list[str]:
    if not arg:
        return []
    want = [x.strip().upper() for x in arg.split(",") if x.strip()]
    out = []
    for c in core.load_cases():
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
    from contest import server
    if not args.no_browser:
        threading_open(f"http://localhost:{port}")
    server.serve(port)
    return 0


def threading_open(url: str) -> None:
    import threading
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()


# ---------------------------------------------------------------------------
def cmd_setup(args) -> int:
    info = core.git_info()
    if not info.get("repo"):
        _p("  git 저장소가 아니에요. 운영 레포를 git clone 한 폴더에서 실행해 주세요.")
        return 1
    if args.git_name:
        core.git("config", "user.name", args.git_name)
    if args.git_email:
        core.git("config", "user.email", args.git_email)
    info = core.git_info()
    ok = True
    _p(f"  git 사용자   {info.get('user') or '없음'} <{info.get('email') or '없음'}>")
    if not info.get("user") or not info.get("email"):
        _p("    → git 사용자 이름·이메일이 필요해요: uv run app.py setup --git-name '이름' --git-email '메일'")
        ok = False
    env = core.ROOT / ".env"
    if not env.exists():
        shutil.copyfile(core.ROOT / ".env.example", env)
        _p("  .env       만들었어요. 파일을 열어 OPENAI_API_KEY= 뒤에 받은 키를 직접 붙여 넣으세요.")
    else:
        _p(f"  .env       있음 · 키 {'들어 있음' if core.load_api_key() else '비어 있음 → 직접 붙여 넣으세요'}")
    core.LOCAL_DIR.mkdir(exist_ok=True)
    prefix = C["branch_prefix"]
    branch = info.get("branch") or ""
    if args.name:
        name = args.name.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", name):
            _p("  이름은 영문 소문자·숫자·하이픈만 써 주세요. 예: gaon, hong-gildong")
            return 1
        target = prefix + name
        if branch != target:
            exists = core.git_ok("rev-parse", "--verify", "--quiet", target)
            r = core.git("switch", target) if exists else core.git("switch", "-c", target)
            if r.returncode != 0:
                _p("  브랜치를 만들지 못했어요: " + r.stderr.strip()[:200])
                return 1
        branch = target
        _p(f"  개인 브랜치  {branch}")
        if info.get("remote"):
            r = core.git("push", "-u", "origin", branch, timeout=60)
            _p("  원격 브랜치  " + ("올렸어요" if r.returncode == 0 else "올리지 못했어요: " + r.stderr.strip()[-200:]))
        else:
            _p("  원격 저장소  없음 (origin이 설정돼 있지 않아요)")
    elif not branch.startswith(prefix):
        _p(f"  개인 브랜치  아직 없어요 → uv run app.py setup --name 영문이름")
        ok = False
    else:
        _p(f"  개인 브랜치  {branch}")
    _p("")
    _p("  다음: 키를 넣은 뒤 uv run app.py 로 화면을 켜세요." if ok else "  위 항목을 마친 뒤 다시 실행해 주세요.")
    return 0 if ok else 1


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
    ids = _case_ids(args.cases)
    if args.cases and not ids:
        _p("  케이스를 찾지 못했어요. 예: --cases P01,P03")
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
    run = engine.Run("pick" if ids else "all", ids, model, effort)
    try:
        recorder.preflight(run.questions)
    except recorder.RecordError as e:
        _p("  " + str(e))
        return 1
    _p(f"  질문 {run.questions}개 생성 · {model} · {effort} · 채점 {C['judge']['model']}")
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
        _p(f"  평가 점수   {r['evalScore']} / 100")
    else:
        _p(f"  질문 점수 평균 {r.get('questionAvg')} · {r.get('evalNote') or ''}")
    for d in C["rubric_display"]:
        _p(f"  {d['id']} {d['name']:<14} {r.get('dimAvg', {}).get(d['id'])} / {d['weight']}")
    if r.get("failCounts"):
        _p("  실패 유형   " + ", ".join(f"{k}×{v}" for k, v in r["failCounts"].items()))
    if r.get("ruleFails"):
        _p("  규칙 진단   " + ", ".join(f"{k}×{v}" for k, v in r["ruleFails"].items()) + " (점수 미반영)")
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
        score = r["evalScore"] if r["evalScore"] is not None else f"질문 평균 {r['questionAvg']}"
        _p(f"  최근 실행   {r['runId']} · {r['scope']} · {score}")
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
    if sc["identical"]:
        _p("  ✗ ③ 질문 생성 프롬프트가 v9.0.0과 같아서 제출할 수 없어요.")
        return 1
    if not sc["ok"]:
        _p(f"  ✗ ③이 v9.0.0과 {pct:.1f}%만 달라요. {sc['min'] * 100:.0f}% 이상 바꿔야 제출할 수 있어요. (공백·빈 줄 차이는 세지 않아요)")
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
    runs = []
    for i in range(args.runs):
        _p(f"  기준선 {i + 1}/{args.runs} 생성 중 …")
        run = engine.Run("all", [], model, effort, prompt_text=prompt, record=False, label=f"baseline-{i + 1}")
        run.execute()
        r = run.result
        qs = {it["itemId"]: it["question"] for c in r["items"] for it in c["items"] if it.get("question")}
        runs.append({"score": r["evalScore"], "questionAvg": r["questionAvg"], "dimAvg": r["dimAvg"], "durationSec": r["durationSec"],
                     "failCounts": r["failCounts"], "ruleFails": r["ruleFails"], "questions": qs, "detail": r})
        _p(f"    평가 점수 {r['evalScore']} · {r['durationSec']}초")
    scores = [x["score"] for x in runs if x["score"] is not None]
    if not scores:
        _p("  평가 점수를 하나도 얻지 못했어요. 오류를 확인해 주세요.")
        return 1
    workers = C.get("workers", 5)
    batches = max(1, -(-len(core.load_cases()) // workers))
    out = {
        "createdAt": recorder.now_iso(),
        "model": model, "effort": effort, "judge": C["judge"],
        "mean": round(sum(scores) / len(scores), 1),
        "spread": round(max(scores) - min(scores), 1),
        "scores": scores,
        "secPerCase": round(sum(x["durationSec"] for x in runs) / len(runs) / batches, 1),
        "runs": runs,
    }
    (core.BASELINE_DIR / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _p(f"  기준 점수 {out['mean']} · 흔들림 폭 {out['spread']} · 패널티 기준 {round(out['mean'] - out['spread'], 1)}")
    _p("  baseline/results.json 에 저장했어요. main에 커밋해 참가자에게 배포하세요.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uv run app.py", description="잡다 프롬프트 콘테스트")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않기")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("setup", help="처음 한 번: .env 준비, 개인 브랜치 만들기")
    p.add_argument("--name", help="영문 이름 (브랜치 contest/이름)")
    p.add_argument("--git-name")
    p.add_argument("--git-email")
    p = sub.add_parser("doctor", help="실행 환경 점검")
    p.add_argument("--ping", action="store_true", help="키가 살아 있는지 확인(비용 없음)")
    p = sub.add_parser("generate", help="화면 없이 생성·채점")
    p.add_argument("--cases", help="케이스 골라 실행. 예: P01,P03")
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
    args = ap.parse_args(argv)
    fn = {"setup": cmd_setup, "doctor": cmd_doctor, "generate": cmd_generate, "status": cmd_status,
          "verify": cmd_verify, "submit": cmd_submit, "baseline": cmd_baseline}.get(args.cmd, cmd_serve)
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
