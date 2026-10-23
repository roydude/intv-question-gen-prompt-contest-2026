"""경로·설정·데이터 로딩과 git 보조 함수."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTEST_DIR = ROOT / "contest"
PROMPTS_DIR = ROOT / "prompts"
BASELINE_DIR = ROOT / "baseline"
GOLDEN_DIR = ROOT / "goldenset"
RUNS_DIR = ROOT / "runs"
WEB_DIR = ROOT / "web"
LOCAL_DIR = ROOT / ".contest"  # PC마다 다른 값(기본 모델 등). git에서 빠진다.
LEDGER = RUNS_DIR / "ledger.jsonl"


def load_config() -> dict:
    return json.loads((CONTEST_DIR / "config.json").read_text(encoding="utf-8"))


CFG = load_config()


# ---------------------------------------------------------------------------
# 골든셋
# ---------------------------------------------------------------------------
def load_cases() -> list[dict]:
    return json.loads((GOLDEN_DIR / "cases_common.json").read_text(encoding="utf-8"))["cases"]


def load_positions() -> dict[str, dict]:
    doc = json.loads((GOLDEN_DIR / "positions.json").read_text(encoding="utf-8"))
    return {p["positionId"]: p for p in doc["positions"]}


def load_rubrics() -> dict:
    return json.loads((GOLDEN_DIR / "rubrics.json").read_text(encoding="utf-8"))


def case_label(case: dict, positions: dict[str, dict]) -> str:
    p = positions.get(case["positionId"]) or {}
    return f"{case['positionId']} {p.get('jobTitleName', '')}".strip()


def case_items(case: dict) -> list[dict]:
    return [it for cat in case["categoriesOverride"] for it in cat["items"]]


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------
def chain_by_id() -> dict[str, dict]:
    return {c["id"]: c for c in CFG["chain"]}


def prompt_path(stage_id: str, baseline: bool = False) -> Path:
    c = chain_by_id().get(stage_id)
    if not c:
        raise KeyError(stage_id)
    return (BASELINE_DIR / "prompts" if baseline else PROMPTS_DIR) / c["file"]


def read_prompt(stage_id: str, baseline: bool = False) -> str:
    return prompt_path(stage_id, baseline).read_text(encoding="utf-8")


def write_prompt(stage_id: str, text: str) -> None:
    p = prompt_path(stage_id)
    p.write_text(text.replace("\r\n", "\n"), encoding="utf-8")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def prompt_hashes() -> dict[str, str]:
    return {c["file"]: sha256_file(PROMPTS_DIR / c["file"]) for c in CFG["chain"]}


def code_hash() -> str:
    """기록·채점에 쓰는 코드와 데이터의 지문. 실행마다 기록에 남겨 코드가 바뀌었는지 보이게 한다."""
    files = [ROOT / "app.py"]
    for d, pat in ((CONTEST_DIR, "*"), (WEB_DIR, "*"), (GOLDEN_DIR, "*.json"), (BASELINE_DIR / "prompts", "*")):
        files += sorted(p for p in d.glob(pat) if p.is_file() and p.suffix not in (".pyc",))
    h = hashlib.sha256()
    for p in files:
        h.update(str(p.relative_to(ROOT)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 형식 영역 (입력·출력 규격) 변경 감지
# ---------------------------------------------------------------------------
FORMAT_WORDS = ("input", "output", "format")


def format_sections(text: str) -> dict[str, str]:
    """'#'로 시작하는 제목 중 Input·Output·Format이 들어간 절의 본문을 모은다."""
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            level = len(s) - len(s.lstrip("#"))
            if level <= 3 and any(w in title.lower() for w in FORMAT_WORDS) and "example" not in title.lower():
                cur = title
                out[cur] = []
                continue
            if level <= 3:
                cur = None
        if cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def format_changes(stage_id: str, text: str) -> list[str]:
    base = format_sections(read_prompt(stage_id, baseline=True))
    mine = format_sections(text)
    return [k for k in base if mine.get(k) != base[k]]


# ---------------------------------------------------------------------------
# v9.0.0 대비 바뀐 분량 (제출 조건)
# ---------------------------------------------------------------------------
def _norm_lines(text: str) -> list[str]:
    """공백·빈 줄 차이는 세지 않도록 줄마다 공백을 하나로 줄이고 빈 줄을 뺀다."""
    out = []
    for line in text.splitlines():
        n = re.sub(r"\s+", " ", line).strip()
        if n:
            out.append(n)
    return out


def change_ratio(stage_id: str = "assignments", text: str | None = None) -> float:
    """v9.0.0 대비 바뀐 글자 수 ÷ v9.0.0 글자 수. 줄 단위로 바뀐 곳을 찾고, 바뀐 줄 묶음 안에서는 글자 단위로 센다."""
    base = _norm_lines(read_prompt(stage_id, baseline=True))
    mine = _norm_lines(text if text is not None else read_prompt(stage_id))
    if base == mine:
        return 0.0
    total = max(1, sum(len(x) for x in base))
    changed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base, mine, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        a, b = "\n".join(base[i1:i2]), "\n".join(mine[j1:j2])
        if not a or not b:
            changed += len(a) + len(b)
            continue
        for t2, a1, a2, b1, b2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if t2 != "equal":
                changed += max(a2 - a1, b2 - b1)
    return changed / total


def submit_check(text: str | None = None) -> dict:
    ratio = change_ratio("assignments", text)
    need = float(CFG.get("submit_min_change", 0.03))
    return {"ratio": round(ratio, 4), "min": need, "identical": ratio == 0, "ok": ratio >= need}


def change_block_message(sc: dict, action: str = "질문을 생성") -> str | None:
    """바뀐 분량이 기준에 못 미치면 막는 이유를 돌려준다. 생성과 제출이 같은 기준을 쓴다."""
    if sc["ok"]:
        return None
    need = f"{round(sc['min'] * 100)}%"
    if sc["identical"]:
        return f"③이 v9.0.0과 같아서 {action}할 수 없어요. {need} 이상 바꿔 주세요."
    return f"③ 바뀐 분량이 {sc['ratio'] * 100:.1f}%예요. {need} 이상 바꿔야 {action}할 수 있어요. 공백·빈 줄 차이는 세지 않아요."


# ---------------------------------------------------------------------------
# 기준선 (v9.0.0)
# ---------------------------------------------------------------------------
def load_baseline() -> dict:
    res_p = BASELINE_DIR / "results.json"
    sample_p = BASELINE_DIR / "v900_questions.json"
    out = {"ready": False, "mean": None, "spread": None, "threshold": None, "questions": {}, "secPerCase": None, "caseAvg": {}}
    if sample_p.exists():
        out["questions"] = json.loads(sample_p.read_text(encoding="utf-8")).get("questions", {})
    if res_p.exists():
        r = json.loads(res_p.read_text(encoding="utf-8"))
        out.update({"ready": True, "mean": r.get("mean"), "spread": r.get("spread"), "createdAt": r.get("createdAt")})
        if out["mean"] is not None:
            out["threshold"] = out["mean"]  # 패널티 선 = 기준 점수(흔들림 폭은 빼지 않는다)
        runs = r.get("runs") or []
        if runs and runs[0].get("questions"):
            out["questions"] = runs[0]["questions"]
        out["secPerCase"] = r.get("secPerCase")
        out["caseAvg"] = r.get("caseAvg") or {}
    return out


# ---------------------------------------------------------------------------
# API 키 (.env) — 값은 어디에도 출력하지 않는다
# ---------------------------------------------------------------------------
def load_api_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if key and key.strip():
        return key.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                return v or None
    return None


def load_base_url() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENAI_BASE_URL="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    return os.environ.get("OPENAI_BASE_URL") or CFG["base_url"]


# ---------------------------------------------------------------------------
# 처음 준비: 개인 브랜치, API 키
# ---------------------------------------------------------------------------
BRANCH_NAME_RE = re.compile(r"[a-z][a-z0-9-]{1,30}")


def slugify(s: str | None) -> str | None:
    s = re.sub(r"[^a-z0-9-]+", "-", (s or "").strip().lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s if BRANCH_NAME_RE.fullmatch(s) else None


_SUGGESTED: dict = {}


def suggest_branch_name() -> str | None:
    """GitHub 로그인 이름(gh) → git 사용자 이름 → 메일 앞부분 순서로 브랜치 이름을 정한다."""
    if "name" in _SUGGESTED:
        return _SUGGESTED["name"]
    name = None
    try:
        r = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, timeout=8)
        if r.returncode == 0:
            name = slugify(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not name:
        name = slugify(git_ok("config", "user.name"))
    if not name:
        name = slugify((git_ok("config", "user.email") or "").split("@")[0])
    _SUGGESTED["name"] = name
    return name


def ensure_branch(name: str | None) -> dict:
    """개인 브랜치(contest/이름)를 만들고 원격에 올린다. 이미 개인 브랜치면 그대로 둔다."""
    prefix = CFG["branch_prefix"]
    info = git_info()
    if not info.get("repo"):
        return {"ok": False, "message": "git 저장소가 아니에요. 운영 레포를 클론한 폴더에서 실행해 주세요."}
    if (info.get("branch") or "").startswith(prefix):
        return {"ok": True, "branch": info["branch"], "created": False, "message": "이미 개인 브랜치예요."}
    name = slugify(name)
    if not name:
        return {"ok": False, "message": "이름은 영문 소문자로 시작하고 영문 소문자·숫자·하이픈만 쓸 수 있어요. 예: roy, hong-gildong"}
    target = prefix + name
    exists = git_ok("rev-parse", "--verify", "--quiet", target)
    r = git("switch", target) if exists else git("switch", "-c", target)
    if r.returncode != 0:
        return {"ok": False, "message": "브랜치를 만들지 못했어요: " + r.stderr.strip()[:200]}
    pushed, msg = False, "원격 저장소가 없어 이 PC에만 만들었어요."
    if info.get("remote"):
        p = git("push", "-u", "origin", target, timeout=60)
        pushed = p.returncode == 0
        msg = "원격에 올렸어요." if pushed else "원격에 올리지 못했어요. 다음 생성 때 다시 올려요."
    return {"ok": True, "branch": target, "created": True, "pushed": pushed, "message": msg}


def key_last4() -> str | None:
    k = load_api_key()
    return k[-4:] if k else None


def write_api_key(key: str) -> None:
    """.env의 OPENAI_API_KEY 줄만 바꾼다. 다른 줄(OPENAI_BASE_URL 등)은 그대로 둔다."""
    env = ROOT / ".env"
    src = env if env.exists() else ROOT / ".env.example"
    lines = src.read_text(encoding="utf-8").splitlines() if src.exists() else []
    out, done = [], False
    for line in lines:
        if line.strip().startswith("OPENAI_API_KEY="):
            out.append(f"OPENAI_API_KEY={key}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"OPENAI_API_KEY={key}")
    env.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(env, 0o600)
    except OSError:
        pass


def try_save_key(raw: str | None) -> dict:
    """붙여 넣은 키를 확인하고 저장한다. 키 값은 돌려주지 않고 끝자리 4자만 돌려준다."""
    from .llm_client import LLMClient  # 순환 import 피하기
    key = (raw or "").strip().strip('"').strip("'")
    if not key or len(key) < 20 or any(ch.isspace() for ch in key):
        return {"ok": False, "saved": False, "message": "키 형식이 아니에요. DM으로 받은 키를 처음부터 끝까지 다시 복사해 주세요."}
    code = LLMClient(key, base_url=load_base_url()).check()
    if code == 401:
        return {"ok": False, "saved": False, "code": code, "message": "키가 맞지 않아요(401). DM으로 받은 키를 처음부터 끝까지 다시 복사해 붙여 넣어 주세요."}
    write_api_key(key)
    last4 = key[-4:]
    if code == 200:
        return {"ok": True, "saved": True, "last4": last4, "message": f"저장했어요 · 연결됨 · 끝자리 {last4}"}
    if code is None:
        return {"ok": True, "saved": True, "last4": last4, "message": f"저장했어요 · 끝자리 {last4} · 네트워크 문제로 연결은 확인하지 못했어요"}
    return {"ok": True, "saved": True, "last4": last4, "code": code,
            "message": f"저장했어요 · 끝자리 {last4} · 연결 확인은 HTTP {code}로 끝까지 하지 못했어요. 생성이 실패하면 운영자에게 알려 주세요"}


# ---------------------------------------------------------------------------
# PC별 설정
# ---------------------------------------------------------------------------
def local_settings() -> dict:
    p = LOCAL_DIR / "local.json"
    d = {"defaultModel": CFG["generation"]["default_model"], "defaultEffort": CFG["generation"]["default_effort"]}
    if p.exists():
        try:
            d.update(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return d


def save_local_settings(d: dict) -> dict:
    LOCAL_DIR.mkdir(exist_ok=True)
    cur = local_settings()
    for k in ("defaultModel", "defaultEffort"):
        if k in d and isinstance(d[k], str):
            cur[k] = d[k]
    (LOCAL_DIR / "local.json").write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur


def valid_model(model: str, effort: str | None) -> str | None:
    """허용되지 않는 조합이면 이유를 돌려준다."""
    g = CFG["generation"]
    if model in g["models"]:
        if effort and effort not in g["models"][model]:
            return f"{model}에서 쓸 수 없는 추론 강도예요: {effort}"
        return None
    if g.get("allow_custom_model") and model.startswith(g.get("custom_model_prefix", "gpt-")):
        return None
    return "GPT 모델만 쓸 수 있어요."


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------
def git(*args: str, check: bool = False, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, check=check)


def git_ok(*args: str) -> str | None:
    try:
        r = git(*args)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def git_info() -> dict:
    inside = git_ok("rev-parse", "--is-inside-work-tree") == "true"
    if not inside:
        return {"repo": False}
    return {
        "repo": True,
        "branch": git_ok("rev-parse", "--abbrev-ref", "HEAD"),
        "user": git_ok("config", "user.name"),
        "email": git_ok("config", "user.email"),
        "remote": git_ok("remote", "get-url", "origin"),
        "head": git_ok("rev-parse", "--short", "HEAD"),
    }
