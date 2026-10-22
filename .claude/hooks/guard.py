"""Claude Code PreToolUse 훅. 콘테스트 실행 기록과 기록 코드를 에이전트가 고치지 못하게 막는다.

- .claude/contest-guard.md에 보호 지침이 남아 있는 동안만 동작한다(API 키 파일 보호는 항상 동작).
- 막을 때는 exit 2와 함께 이유를 stderr로 돌려준다.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2]).resolve()
GUARD_FILE = ROOT / ".claude" / "contest-guard.md"
GUARD_MARK = "실행 기록 보호 지침"
EDITABLE = ("prompts", "scratch")
HOME = Path.home().resolve()
CLAUDE_SETTINGS = {HOME / ".claude" / "settings.json", HOME / ".claude" / "settings.local.json", HOME / ".claude.json"}

MSG_GUARD = "이 레포의 콘테스트 규칙상 실행 기록과 기록 코드는 고칠 수 없어요. 고칠 수 있는 곳은 prompts/와 scratch/뿐이에요."
MSG_ENV = "API 키 파일(.env)은 Claude Code가 열거나 고칠 수 없어요. 참가자가 편집기로 직접 열어 키를 넣어 주세요."

PROTECTED_RE = re.compile(
    r"(?<![\w-])(runs|\.claude|contest|app\.py|web|goldenset|baseline|CLAUDE\.md|README\.md|\.gitignore|"
    r"ledger\.jsonl|contest-guard\.md|settings\.json|settings\.local\.json|\.claude\.json)(?![\w-])"
)
ENV_RE = re.compile(r"(?<![\w.])\.env(?![\w.])")
WRITE_CMDS = {"rm", "rmdir", "mv", "cp", "truncate", "tee", "chmod", "chown", "ln", "touch", "dd", "unlink", "install", "rsync", "shred"}
INTERPRETERS = re.compile(r"(?<![\w-])(python3?|node|ruby|perl|php|osascript|deno|bun)(?![\w-])|uv\s+run\s+(--\S+\s+)*python|(ba|z)?sh\s+-c")
WRITE_API = re.compile(r"open\(|write|unlink|remove|rmtree|rename|replace\(|truncate|shutil|os\.system|subprocess|writeFile|appendFile|fs\.|Path\(|\bmv\b|\brm\b")
GIT_ALWAYS_BLOCK = {"reset", "rebase", "filter-branch", "filter-repo", "update-ref", "replace", "reflog", "gc", "prune",
                    "revert", "stash", "clean", "rm", "mv", "restore", "apply", "am", "notes"}


def guard_on() -> bool:
    try:
        return GUARD_MARK in GUARD_FILE.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def deny(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def resolve(p: str) -> Path:
    path = Path(os.path.expanduser(p))
    if not path.is_absolute():
        path = Path(os.getcwd()) / path
    return path.resolve()


def check_path(p: str | None, on: bool) -> None:
    if not p:
        return
    rp = resolve(p)
    if rp.name == ".env" or rp.name.startswith(".env.") and rp.name != ".env.example":
        deny(MSG_ENV)
    if not on:
        return
    if rp in CLAUDE_SETTINGS:
        deny(MSG_GUARD)
    try:
        rel = rp.relative_to(ROOT)
    except ValueError:
        return  # 레포 밖(임시 폴더 등)은 막지 않는다
    if not rel.parts or rel.parts[0] not in EDITABLE:
        deny(MSG_GUARD)


def segments(cmd: str) -> list[str]:
    return [s.strip() for s in re.split(r"&&|\|\||;|\||\n", cmd) if s.strip()]


def tokens(seg: str) -> list[str]:
    try:
        return shlex.split(seg)
    except ValueError:
        return seg.split()


def check_git(toks: list[str]) -> None:
    i = toks.index("git") + 1
    while i < len(toks) and toks[i].startswith("-"):
        i += 2 if toks[i] in ("-c", "-C") else 1
    if i >= len(toks):
        return
    sub, rest = toks[i], toks[i + 1:]
    joined = " ".join(rest)
    if sub in GIT_ALWAYS_BLOCK:
        deny(MSG_GUARD)
    if sub == "commit" and re.search(r"--amend|--fixup|--squash", joined):
        deny(MSG_GUARD)
    if sub == "push" and (re.search(r"(^|\s)(-f|--force\S*|--delete|-d|--mirror|--prune)(\s|$)", joined) or re.search(r"(^|\s)[+:]\S", joined)):
        deny(MSG_GUARD)
    if sub == "branch" and re.search(r"(^|\s)(-D|-d|--delete|-m|-M|--move|-f|--force|-c|-C)(\s|$)", joined):
        deny(MSG_GUARD)
    if sub == "tag" and re.search(r"(^|\s)(-d|--delete|-f|--force)(\s|$)", joined):
        deny(MSG_GUARD)
    if sub in ("checkout", "switch") and ("--" in rest or "-f" in rest or "--force" in rest or "--orphan" in rest
                                         or any(PROTECTED_RE.search(t) and not t.startswith("contest/") for t in rest)):
        deny(MSG_GUARD)
    if sub == "config" and re.search(r"core\.hooksPath|alias\.", joined):
        deny(MSG_GUARD)


def check_bash(cmd: str, on: bool) -> None:
    if ENV_RE.search(cmd.replace(".env.example", "")):
        deny(MSG_ENV)
    if not on:
        return
    if re.search(r"disableAllHooks|claude\s+config|CLAUDE_PROJECT_DIR", cmd):
        deny(MSG_GUARD)
    # 리다이렉션 대상이 보호 경로면 막는다 (2>&1, >/dev/null 등은 통과)
    for m in re.finditer(r"\d*>>?\s*([^\s&|;<>]+)", cmd):
        target = m.group(1)
        if target.startswith("&") or target.startswith("/dev/"):
            continue
        if PROTECTED_RE.search(target) or ENV_RE.search(target):
            deny(MSG_GUARD)
    # 인터프리터로 보호 경로를 쓰는 경우
    if INTERPRETERS.search(cmd) and PROTECTED_RE.search(cmd) and WRITE_API.search(cmd) and not re.match(r"^\s*uv\s+run\s+app\.py\b", cmd):
        deny(MSG_GUARD)
    for seg in segments(cmd):
        toks = tokens(seg)
        if not toks:
            continue
        if "git" in toks:
            check_git(toks)
            continue
        head = os.path.basename(toks[0])
        if head in ("sudo", "env", "command", "xargs", "nohup", "time"):
            toks = toks[1:] or toks
            head = os.path.basename(toks[0])
        body = " ".join(toks[1:])
        if head in WRITE_CMDS and PROTECTED_RE.search(body):
            deny(MSG_GUARD)
        if head in ("sed", "gsed", "perl") and re.search(r"(^|\s)-[a-zA-Z]*i|--in-place", body) and PROTECTED_RE.search(body):
            deny(MSG_GUARD)
        if head == "find" and re.search(r"-delete|-exec|-execdir", body) and PROTECTED_RE.search(body):
            deny(MSG_GUARD)
        if head in ("awk", "gawk") and "inplace" in body and PROTECTED_RE.search(body):
            deny(MSG_GUARD)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    tool = data.get("tool_name") or ""
    inp = data.get("tool_input") or {}
    on = guard_on()
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        check_path(inp.get("file_path") or inp.get("notebook_path"), on)
    elif tool == "Bash":
        check_bash(inp.get("command") or "", on)


if __name__ == "__main__":
    main()
