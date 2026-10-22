"""경로·설정·데이터 로딩과 git 보조 함수."""
from __future__ import annotations

import hashlib
import json
import os
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
# 기준선 (v9.0.0)
# ---------------------------------------------------------------------------
def load_baseline() -> dict:
    res_p = BASELINE_DIR / "results.json"
    sample_p = BASELINE_DIR / "v900_questions.json"
    out = {"ready": False, "mean": None, "spread": None, "threshold": None, "questions": {}, "secPerCase": None}
    if sample_p.exists():
        out["questions"] = json.loads(sample_p.read_text(encoding="utf-8")).get("questions", {})
    if res_p.exists():
        r = json.loads(res_p.read_text(encoding="utf-8"))
        out.update({"ready": True, "mean": r.get("mean"), "spread": r.get("spread"), "createdAt": r.get("createdAt")})
        if out["mean"] is not None and out["spread"] is not None:
            out["threshold"] = round(out["mean"] - out["spread"], 1)
        runs = r.get("runs") or []
        if runs and runs[0].get("questions"):
            out["questions"] = runs[0]["questions"]
        out["secPerCase"] = r.get("secPerCase")
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
