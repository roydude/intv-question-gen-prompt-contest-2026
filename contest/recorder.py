"""실행 기록(runs/ledger.jsonl)과 자동 커밋·푸시.

- 생성할 때마다 시작·완료 두 줄을 남긴다. 줄마다 앞 줄의 해시를 담아 중간 줄을 고치거나 지우면 바로 드러난다.
- 시작·완료 때마다 runs/와 prompts/를 커밋하고 개인 브랜치로 푸시한다. 브랜치는 강제 푸시가 막혀 있어 올라간 기록은 남는다.
- 남은 질문 수는 이 기록으로만 계산한다.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone, timedelta

from . import core

KST = timezone(timedelta(hours=9))
_lock = threading.Lock()
_push_lock = threading.Lock()
PUSH_STATE = {"state": "idle", "message": "", "at": None}


class RecordError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return core.sha256_text(json.dumps(body, ensure_ascii=False, sort_keys=True))


# ---------------------------------------------------------------------------
# 읽기·검증
# ---------------------------------------------------------------------------
def read_ledger() -> list[dict]:
    if not core.LEDGER.exists():
        return []
    rows = []
    for i, line in enumerate(core.LEDGER.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise RecordError(f"실행 기록 {i}번째 줄을 읽을 수 없어요.") from e
    return rows


def verify() -> dict:
    """기록이 고쳐지지 않았는지 확인한다. 문제가 있으면 problems에 담는다."""
    problems: list[str] = []
    try:
        rows = read_ledger()
    except RecordError as e:
        return {"ok": False, "problems": [str(e)], "rows": 0}
    prev = ""
    for i, r in enumerate(rows, 1):
        if r.get("seq") != i:
            problems.append(f"{i}번째 줄의 순번이 맞지 않아요.")
        if r.get("prev") != prev:
            problems.append(f"{i}번째 줄이 앞 줄과 이어지지 않아요.")
        if r.get("hash") != _entry_hash(r):
            problems.append(f"{i}번째 줄의 내용이 바뀌었어요.")
        if r.get("event") == "finish" and r.get("result"):
            p = core.ROOT / r["result"]
            if not p.exists():
                problems.append(f"{r['result']} 파일이 없어요.")
            elif core.sha256_file(p) != r.get("resultSha256"):
                problems.append(f"{r['result']} 파일 내용이 바뀌었어요.")
        prev = r.get("hash", "")
    problems += _history_problems()
    return {"ok": not problems, "problems": problems, "rows": len(rows)}


def _history_problems() -> list[str]:
    """예전 커밋의 기록이 지금 기록의 앞부분으로 그대로 남아 있는지 본다. 끝의 줄을 지우고 커밋한 경우를 잡는다."""
    current = core.LEDGER.read_text(encoding="utf-8") if core.LEDGER.exists() else ""
    out = core.git_ok("log", "--format=%H", "-n", "300", "--", "runs/ledger.jsonl")
    for h in (out or "").split():
        r = core.git("show", f"{h}:runs/ledger.jsonl")
        if r.returncode != 0:
            continue
        if not current.startswith(r.stdout):
            return [f"예전 커밋({h[:7]})에 있던 기록이 지금 기록에서 사라졌어요."]
    return []


def usage() -> dict:
    """사용한 질문 수. 완료 기록이 있으면 실제 차감 수, 없으면(중간에 꺼짐) 시작할 때 잡은 수를 센다."""
    rows = read_ledger()
    starts = {r["runId"]: r for r in rows if r.get("event") == "start"}
    finishes = {r["runId"]: r for r in rows if r.get("event") == "finish"}
    used = 0
    for rid, s in starts.items():
        f = finishes.get(rid)
        used += int(f.get("charged", s.get("questions", 0))) if f else int(s.get("questions", 0))
    limit = int(core.CFG["limit_questions"])
    return {"used": used, "limit": limit, "remaining": max(0, limit - used), "runs": len(starts)}


def runs_dirty() -> list[str]:
    """runs/에서 커밋되지 않은 경로 목록."""
    try:
        r = core.git("status", "--porcelain", "--", "runs")
    except OSError:
        return []
    return [ln[3:] for ln in r.stdout.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# 쓰기
# ---------------------------------------------------------------------------
def _append(entry: dict) -> dict:
    rows = read_ledger()
    entry = dict(entry)
    entry["seq"] = len(rows) + 1
    entry["prev"] = rows[-1]["hash"] if rows else ""
    entry["ts"] = now_iso()
    entry["hash"] = _entry_hash(entry)
    core.RUNS_DIR.mkdir(exist_ok=True)
    with core.LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def preflight(questions: int) -> None:
    """생성 전에 막아야 할 상황을 확인한다."""
    info = core.git_info()
    if not info.get("repo"):
        raise RecordError("git 저장소가 아니에요. 레포를 클론한 폴더에서 실행해 주세요.")
    prefix = core.CFG["branch_prefix"]
    if not (info.get("branch") or "").startswith(prefix):
        raise RecordError(f"개인 브랜치({prefix}영문이름)에서만 생성할 수 있어요. Claude Code에서 /시작을 먼저 해 주세요.")
    if not info.get("user") or not info.get("email"):
        raise RecordError("git 사용자 이름·이메일이 설정돼 있지 않아요. /시작에서 설정해 주세요.")
    v = verify()
    if not v["ok"]:
        raise RecordError("실행 기록이 손상됐어요. 운영자에게 알려 주세요. (" + "; ".join(v["problems"][:3]) + ")")
    dirty = runs_dirty()
    if dirty:
        raise RecordError("runs/에 커밋되지 않은 변경이 있어요. 기록 파일은 고칠 수 없어요. 운영자에게 알려 주세요.")
    u = usage()
    if questions > u["remaining"]:
        raise RecordError(f"남은 질문이 {u['remaining']}개라 {questions}개를 만들 수 없어요.")


def record_start(run: dict) -> dict:
    with _lock:
        preflight(run["questions"])
        info = core.git_info()
        entry = _append({
            "event": "start",
            "runId": run["runId"],
            "branch": info.get("branch"),
            "user": f"{info.get('user')} <{info.get('email')}>",
            "scope": run["scope"],
            "cases": run["cases"],
            "questions": run["questions"],
            "model": run["model"],
            "effort": run["effort"],
            "judgeModel": core.CFG["judge"]["model"],
            "prompts": core.prompt_hashes(),
            "code": core.code_hash(),
        })
        _commit(f"기록: 실행 {run['runId']} 시작 · 질문 {run['questions']}개", ["runs", "prompts"])
    push_async()
    return entry


def record_finish(run_id: str, status: str, charged: int, score: float | None, result_rel: str, avg: float | None = None) -> dict:
    with _lock:
        entry = _append({
            "event": "finish",
            "runId": run_id,
            "status": status,
            "charged": charged,
            "score": score,
            "result": result_rel,
            "resultSha256": core.sha256_file(core.ROOT / result_rel),
        })
        if score is not None:
            label = f"평가 점수 {score}"
        elif avg is not None:
            label = f"질문 평균 {avg}" + (" · 중지됨" if status == "stopped" else "")
        else:
            label = "중지됨" if status == "stopped" else "점수 없음"
        _commit(f"기록: 실행 {run_id} 완료 · {label}", ["runs"])
    push_async()
    return entry


def _commit(message: str, paths: list[str]) -> None:
    r = core.git("add", "--", *paths)
    if r.returncode != 0:
        raise RecordError("기록을 git에 올리지 못했어요: " + r.stderr.strip()[:200])
    r = core.git("commit", "--no-verify", "-m", message, "--", *paths)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        raise RecordError("기록 커밋에 실패했어요: " + (r.stderr or r.stdout).strip()[:200])


# ---------------------------------------------------------------------------
# 푸시
# ---------------------------------------------------------------------------
def ahead_count() -> int | None:
    out = core.git_ok("rev-list", "--count", "@{u}..HEAD")
    return int(out) if out and out.isdigit() else None


def push_now() -> dict:
    with _push_lock:
        info = core.git_info()
        if not info.get("remote"):
            PUSH_STATE.update(state="no-remote", message="원격 저장소가 없어 커밋만 했어요.", at=now_iso())
            return dict(PUSH_STATE)
        PUSH_STATE.update(state="pushing", message="기록을 올리는 중", at=now_iso())
        try:
            r = core.git("push", "-u", "origin", "HEAD", timeout=60)
        except Exception as e:  # noqa: BLE001
            PUSH_STATE.update(state="failed", message=f"푸시 실패: {e}", at=now_iso())
            return dict(PUSH_STATE)
        if r.returncode == 0:
            PUSH_STATE.update(state="pushed", message="기록이 개인 브랜치에 올라갔어요.", at=now_iso())
        else:
            PUSH_STATE.update(state="failed", message="푸시 실패 · 다음 실행 때 다시 올려요: " + r.stderr.strip()[-200:], at=now_iso())
        return dict(PUSH_STATE)


def push_async() -> None:
    threading.Thread(target=push_now, daemon=True).start()


def push_pending_on_start() -> None:
    """앱을 켤 때 아직 못 올린 기록 커밋이 있으면 올린다."""
    n = ahead_count()
    if n:
        push_async()


def wait_push(timeout: float = 60) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if PUSH_STATE["state"] != "pushing" and not _push_lock.locked():
            break
        time.sleep(0.3)
    return dict(PUSH_STATE)
