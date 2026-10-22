"""로컬 화면용 HTTP 서버 (표준 라이브러리, 127.0.0.1 전용)."""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import core, engine, recorder

_KEY_STATE = {"state": None, "message": ""}


def _state() -> dict:
    cfg = core.CFG
    info = core.git_info()
    try:
        u = recorder.usage()
    except recorder.RecordError as e:
        u = {"used": None, "limit": cfg["limit_questions"], "remaining": 0, "runs": 0, "error": str(e)}
    run = engine.CURRENT["run"]
    results = engine.list_results()
    key = core.load_api_key()
    return {
        "title": cfg["title"],
        "deadline": cfg["deadline_label"],
        "limit": cfg["limit_questions"],
        "qpc": cfg["questions_per_case"],
        "chain": cfg["chain"],
        "models": cfg["generation"]["models"],
        "allowCustomModel": cfg["generation"].get("allow_custom_model", False),
        "judge": {"model": cfg["judge"]["model"], "effort": cfg["judge"]["effort"], "label": cfg["judge"]["label"]},
        "rubric": cfg["rubric_display"],
        "failTypes": cfg["fail_types"],
        "rules": cfg["rules_display"],
        "git": info,
        "branchOk": (info.get("branch") or "").startswith(cfg["branch_prefix"]),
        "branchPrefix": cfg["branch_prefix"],
        "usage": u,
        "key": {"present": bool(key), "checked": _KEY_STATE["state"], "message": _KEY_STATE["message"]},
        "local": core.local_settings(),
        "running": run.snapshot() if run is not None else None,
        "latest": results[-1] if results else None,
        "latestFull": next((r for r in reversed(results) if r.get("evalScore") is not None), None),
        "baseline": {k: v for k, v in core.load_baseline().items() if k != "questions"},
        "push": dict(recorder.PUSH_STATE),
        "workers": cfg.get("workers", 5),
    }


def _cases() -> list[dict]:
    positions = core.load_positions()
    out = []
    for c in core.load_cases():
        p = positions[c["positionId"]]
        cm = (c.get("experience") or {}).get("careerMemory") or {}
        cat = c["categoriesOverride"][0]
        out.append({
            "caseId": c["caseId"],
            "label": core.case_label(c, positions),
            "job": p.get("jobTitleName"),
            "who": c.get("scenarioLabel", ""),
            "persona": c.get("personaSummary", ""),
            "position": p["positionInfo"],
            "careers": cm.get("careers", []),
            "projects": cm.get("projects", []),
            "activities": cm.get("activities", []),
            "educations": cm.get("educations", []),
            "notes": cm.get("notes", []),
            "skills": cm.get("skills", []),
            "resume": (c.get("experience") or {}).get("resumeInfo", ""),
            "category": {"name": cat["categoryName"], "definition": cat.get("categoryDefinition", "")},
            "items": [{"itemId": it["itemId"], "name": it["itemName"], "definition": it.get("itemDefinition", "")} for it in cat["items"]],
            "forbidden": (c.get("expectations") or {}).get("forbiddenTerms", []),
        })
    return out


def _prompts() -> dict:
    out = {}
    for c in core.CFG["chain"]:
        out[c["id"]] = {"file": c["file"], "mine": core.read_prompt(c["id"]), "base": core.read_prompt(c["id"], baseline=True)}
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "contest/1.0"

    def log_message(self, fmt, *args):  # 조용히
        return

    # -------------------------------------------------- helpers
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _static(self, rel: str):
        p = (core.WEB_DIR / rel).resolve()
        if core.WEB_DIR.resolve() not in p.parents or not p.is_file():
            self.send_error(404)
            return
        data = p.read_bytes()
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -------------------------------------------------- GET
    def do_GET(self):
        if not self._host_ok():
            self.send_error(403)
            return
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)
        if path in ("/", "/result", "/index.html"):
            return self._static("index.html")
        if path == "/api/state":
            return self._json(_state())
        if path == "/api/prompts":
            return self._json(_prompts())
        if path == "/api/cases":
            return self._json(_cases())
        if path == "/api/run/current":
            run = engine.CURRENT["run"]
            return self._json(run.snapshot() if run else None)
        if path == "/api/results":
            return self._json(engine.list_results())
        if path == "/api/result":
            rid = (q.get("id") or [None])[0]
            return self._json(engine.load_result(rid))
        if path == "/api/estimate":
            n = int((q.get("cases") or ["10"])[0])
            return self._json({"seconds": engine.estimate_seconds(n)})
        if path.startswith("/api/"):
            return self._json({"error": "없는 주소예요."}, 404)
        return self._static(path.lstrip("/"))

    # -------------------------------------------------- POST
    def do_POST(self):
        if not self._host_ok():
            self.send_error(403)
            return
        path = urlparse(self.path).path
        b = self._body()
        if path == "/api/prompts/save":
            sid = b.get("id")
            text = b.get("text")
            if sid not in core.chain_by_id() or not isinstance(text, str):
                return self._json({"error": "저장할 프롬프트를 찾지 못했어요."}, 400)
            run = engine.CURRENT["run"]
            if run is not None and run.status == "running":
                return self._json({"error": "생성 중에는 프롬프트를 고칠 수 없어요."}, 409)
            core.write_prompt(sid, text)
            return self._json({"ok": True, "formatChanged": core.format_changes(sid, text)})
        if path == "/api/run":
            scope = b.get("scope") or "all"
            model = (b.get("model") or "").strip()
            effort = b.get("effort") or None
            err = core.valid_model(model, effort)
            if err:
                return self._json({"error": err}, 400)
            try:
                run = engine.start_background(scope, b.get("cases") or [], model, effort)
            except (RuntimeError, ValueError) as e:
                return self._json({"error": str(e)}, 409)
            return self._json(run.snapshot())
        if path == "/api/run/stop":
            run = engine.CURRENT["run"]
            if run is not None and run.status == "running":
                run.stop_flag.set()
            return self._json({"ok": True})
        if path == "/api/settings":
            model = b.get("defaultModel")
            effort = b.get("defaultEffort")
            if model:
                err = core.valid_model(model, effort)
                if err:
                    return self._json({"error": err}, 400)
            return self._json(core.save_local_settings(b))
        if path == "/api/key/check":
            ok, msg = engine.make_client().ping()
            _KEY_STATE.update(state="ok" if ok else "fail", message=msg)
            return self._json({"ok": ok, "message": msg})
        if path == "/api/push":
            return self._json(recorder.push_now())
        return self._json({"error": "없는 주소예요."}, 404)


def serve(port: int) -> None:
    recorder.push_pending_on_start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    print(f"  로컬 화면   http://localhost:{port}", flush=True)
    try:
        u = recorder.usage()
        print(f"  남은 질문   {u['remaining']} / {u['limit']}", flush=True)
    except recorder.RecordError as e:
        print(f"  기록 확인 필요: {e}", flush=True)
    print("  끄려면 Ctrl+C", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  화면을 껐어요.")
