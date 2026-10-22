"""질문 생성 → 규칙 검사 → AI 채점을 한 번에 돌리고 결과를 모은다.

화면(server.py)과 터미널 명령(app.py generate)이 같은 엔진을 쓴다.
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import core, recorder
from .checks import check_assignments, parse_json, resolve_assignments_input
from .judge import build_judge, parse_judge
from .llm_client import LLMClient, LLMError, estimate_cost_usd

_current_lock = threading.Lock()
CURRENT: dict = {"run": None}


def build_input(case: dict, position: dict) -> dict:
    position_info, categories = resolve_assignments_input(case, position)
    return {"positionInfo": position_info, "categories": categories, "experience": case["experience"]}


def make_client() -> LLMClient:
    return LLMClient(core.load_api_key(), base_url=core.load_base_url(), api=core.CFG.get("api", "responses"),
                     timeout=core.CFG.get("timeout_sec", 240))


class Run:
    def __init__(self, scope: str, case_ids: list[str], model: str, effort: str | None,
                 prompt_text: str | None = None, record: bool = True, label: str | None = None):
        self.cases_all = core.load_cases()
        self.positions = core.load_positions()
        self.rubrics = core.load_rubrics()
        by_id = {c["caseId"]: c for c in self.cases_all}
        self.case_ids = [c for c in case_ids if c in by_id] if scope == "pick" else [c["caseId"] for c in self.cases_all]
        if not self.case_ids:
            raise ValueError("실행할 케이스를 하나 이상 골라 주세요.")
        self.cases = [by_id[c] for c in self.case_ids]
        self.scope = "pick" if scope == "pick" and len(self.case_ids) < len(self.cases_all) else "all"
        self.model = model
        self.effort = effort
        self.record = record
        self.label = label
        self.prompt_text = prompt_text if prompt_text is not None else core.read_prompt("assignments")
        self.run_id = label or datetime.now(recorder.KST).strftime("%y%m%d-%H%M%S")
        self.qpc = int(core.CFG["questions_per_case"])
        self.questions = self.qpc * len(self.cases)
        self.status = "ready"
        self.message = ""
        self.stop_flag = threading.Event()
        self.started_at = None
        self.finished_at = None
        self.attempted: set[str] = set()
        self.records: dict[str, dict] = {}
        self.progress = {c["caseId"]: {"gen": "wait", "check": "wait", "judge": "wait"} for c in self.cases}
        self.result_rel = None
        self.result = None

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        n = len(self.cases)
        p = self.progress.values()
        return {
            "runId": self.run_id,
            "status": self.status,
            "message": self.message,
            "scope": self.scope,
            "model": self.model,
            "effort": self.effort,
            "startedAt": self.started_at,
            "questions": self.questions,
            "total": n,
            "genDone": sum(1 for x in p if x["gen"] in ("done", "error")),
            "checkDone": sum(1 for x in p if x["check"] == "done"),
            "judgeDone": sum(1 for x in p if x["judge"] in ("done", "error", "skip")),
            "cases": [{"caseId": c["caseId"], "label": core.case_label(c, self.positions), "who": c.get("scenarioLabel", ""),
                       **self.progress[c["caseId"]]} for c in self.cases],
            "result": self.result_rel,
        }

    # ------------------------------------------------------------------
    def _work(self, case: dict, client: LLMClient) -> None:
        cid = case["caseId"]
        if self.stop_flag.is_set():
            self.progress[cid].update(gen="skip", check="skip", judge="skip")
            return
        self.attempted.add(cid)
        position = self.positions[case["positionId"]]
        user_input = build_input(case, position)
        rec: dict = {"caseId": cid}
        g = core.CFG["generation"]
        self.progress[cid]["gen"] = "running"
        try:
            r = client.complete(self.model, self.prompt_text, json.dumps(user_input, ensure_ascii=False, indent=2),
                                reasoning_effort=self.effort, json_mode=True, max_output_tokens=g["max_output_tokens"])
            rec["outputText"] = r["text"]
            rec["gen"] = {k: r.get(k) for k in ("model", "effort", "usage", "response_id", "latency_ms")}
            self.progress[cid]["gen"] = "done"
        except LLMError as e:
            rec["genError"] = str(e)
            self.progress[cid].update(gen="error", check="skip", judge="skip")
            self.records[cid] = rec
            return
        rec["checks"] = check_assignments(rec["outputText"], case, position).to_dict()
        self.progress[cid]["check"] = "done"
        obj, _ = parse_json(rec["outputText"])
        if obj is None:
            self.progress[cid]["judge"] = "skip"
            self.records[cid] = rec
            return
        self.progress[cid]["judge"] = "running"
        j = core.CFG["judge"]
        sys_p, user_p = build_judge(case, position, rec["outputText"], self.rubrics)
        last = None
        for _ in range(2):
            try:
                jr = client.complete(j["model"], sys_p, user_p, reasoning_effort=j["effort"], json_mode=True,
                                     max_output_tokens=j["max_output_tokens"])
                parsed = parse_judge(jr["text"], self.rubrics)
                if parsed["ok"]:
                    rec["judge"] = parsed
                    rec["judgeUsage"] = jr.get("usage")
                    break
                last = parsed.get("error")
            except LLMError as e:
                last = str(e)
        if "judge" in rec:
            self.progress[cid]["judge"] = "done"
        else:
            rec["judgeError"] = last
            self.progress[cid]["judge"] = "error"
        self.records[cid] = rec

    # ------------------------------------------------------------------
    def execute(self) -> dict:
        self.status = "running"
        self.started_at = recorder.now_iso()
        t0 = time.time()
        if self.record:
            try:
                recorder.record_start({"runId": self.run_id, "scope": self.scope, "cases": self.case_ids,
                                       "questions": self.questions, "model": self.model, "effort": self.effort})
            except recorder.RecordError as e:
                self.status, self.message = "error", str(e)
                return self.snapshot()
        client = make_client()
        with ThreadPoolExecutor(max_workers=core.CFG.get("workers", 5)) as ex:
            list(ex.map(lambda c: self._work(c, client), self.cases))
        self.finished_at = recorder.now_iso()
        stopped = self.stop_flag.is_set() and len(self.attempted) < len(self.cases)
        self.result = self._aggregate(time.time() - t0, stopped)
        self.status = "stopped" if stopped else "done"
        if self.record:
            core.RUNS_DIR.mkdir(exist_ok=True)
            self.result_rel = f"runs/{self.run_id}.json"
            (core.ROOT / self.result_rel).write_text(json.dumps(self.result, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                recorder.record_finish(self.run_id, self.status, self.result["charged"], self.result["evalScore"], self.result_rel,
                                       self.result["questionAvg"])
            except recorder.RecordError as e:
                self.message = str(e)
        return self.snapshot()

    # ------------------------------------------------------------------
    def _aggregate(self, duration: float, stopped: bool) -> dict:
        baseline = core.load_baseline()
        dims = core.CFG["rubric_display"]
        cases_out, all_totals, judge_missing, infra_error = [], [], False, False
        fail_counts, rule_fails = Counter(), Counter()
        dim_vals: dict[str, list[float]] = {d["id"]: [] for d in dims}
        usage = {"input": 0, "output": 0, "reasoning": 0}
        cost = 0.0
        prices = core.CFG.get("prices_usd_per_1m", {})
        for case in self.cases:
            cid = case["caseId"]
            rec = self.records.get(cid, {})
            out_obj, _ = parse_json(rec.get("outputText", "")) if rec.get("outputText") else (None, None)
            ivs = {iv.get("itemId"): iv for iv in (out_obj or {}).get("interviews", []) if isinstance(iv, dict)} if isinstance(out_obj, dict) else {}
            judged = {it.get("itemId"): it for it in (rec.get("judge") or {}).get("items", [])}
            judged_list = (rec.get("judge") or {}).get("items", [])
            checks = rec.get("checks") or {}
            for fid in checks.get("failed", []):
                rule_fails[fid] += 1
            for u, m in ((rec.get("gen") or {}).get("usage"), self.model), (rec.get("judgeUsage"), core.CFG["judge"]["model"]):
                if u:
                    for k in usage:
                        usage[k] += u.get(k, 0) or 0
                    cost += estimate_cost_usd(u, m, prices)
            items_out = []
            for i, it in enumerate(core.case_items(case)):
                iid = it["itemId"]
                iv = ivs.get(iid) or {}
                jd = judged.get(iid) or (judged_list[i] if i < len(judged_list) else None)
                note = None
                if cid not in self.attempted:
                    total, note = None, "실행하지 않음"
                elif rec.get("genError"):
                    total, note, infra_error = None, "생성 실패(네트워크·API)", True
                elif out_obj is None:
                    total, note = 0.0, "출력이 JSON이 아니에요"
                elif not iv.get("question"):
                    total, note = 0.0, "이 평가 요소의 질문이 출력에 없어요"
                elif jd is None or jd.get("total") is None:
                    total, note, judge_missing = None, "채점 실패", True
                else:
                    total = jd["total"]
                if jd and iv.get("question"):
                    for d in dims:
                        p = (jd.get("points") or {}).get(d["id"])
                        if p is not None:
                            dim_vals[d["id"]].append(p)
                    for code in jd.get("failCodes", []):
                        fail_counts[code] += 1
                if total is not None:
                    all_totals.append(total)
                items_out.append({
                    "itemId": iid,
                    "itemName": it["itemName"],
                    "question": iv.get("question"),
                    "answerTip": iv.get("answerTip"),
                    "baselineQuestion": baseline["questions"].get(iid),
                    "total": total,
                    "note": note,
                    "points": (jd or {}).get("points"),
                    "reasons": {k: (v or {}).get("reason") for k, v in ((jd or {}).get("scores") or {}).items()},
                    "failCodes": (jd or {}).get("failCodes", []),
                    "issues": (jd or {}).get("issues", []),
                })
            vals = [x["total"] for x in items_out if x["total"] is not None]
            cases_out.append({
                "caseId": cid,
                "label": core.case_label(case, self.positions),
                "who": case.get("scenarioLabel", ""),
                "category": case["categoriesOverride"][0]["categoryName"],
                "avg": round(sum(vals) / len(vals), 1) if vals else None,
                "items": items_out,
                "checks": {"passed": checks.get("passed"), "failed": checks.get("failed", []), "hard": checks.get("hard", []),
                           "warnings": checks.get("warnings", []), "disqualified": checks.get("disqualified", False)},
                "genError": rec.get("genError"),
                "judgeError": rec.get("judgeError"),
                "judgeComment": (rec.get("judge") or {}).get("comment"),
                "outputText": rec.get("outputText"),
            })
        full = self.scope == "all" and not stopped and not judge_missing and not infra_error
        eval_score = round(sum(all_totals) / len(all_totals), 1) if full and all_totals else None
        reason = None
        if self.scope != "all":
            reason = "특정 케이스만 실행해서 평가 점수는 계산하지 않았어요."
        elif stopped:
            reason = "중간에 멈춰서 평가 점수는 계산하지 않았어요."
        elif infra_error or judge_missing:
            reason = "생성이나 채점이 실패한 질문이 있어 평가 점수를 계산하지 못했어요. 다시 실행해 주세요."
        return {
            "runId": self.run_id,
            "scope": self.scope,
            "cases": self.case_ids,
            "model": self.model,
            "effort": self.effort,
            "judge": {"model": core.CFG["judge"]["model"], "effort": core.CFG["judge"]["effort"]},
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "durationSec": round(duration, 1),
            "status": "stopped" if stopped else "done",
            "charged": len(self.attempted) * self.qpc,
            "questionAvg": round(sum(all_totals) / len(all_totals), 1) if all_totals else None,
            "evalScore": eval_score,
            "evalNote": reason,
            "dimAvg": {k: (round(sum(v) / len(v), 1) if v else None) for k, v in dim_vals.items()},
            "failCounts": dict(fail_counts.most_common()),
            "ruleFails": dict(rule_fails.most_common()),
            "prompts": core.prompt_hashes() if self.record else None,
            "code": core.code_hash(),
            "head": core.git_ok("rev-parse", "--short", "HEAD"),
            "usage": usage,
            "costUsd": round(cost, 4),
            "items": cases_out,
        }


# ---------------------------------------------------------------------------
# 화면에서 쓰는 진입점
# ---------------------------------------------------------------------------
def start_background(scope: str, case_ids: list[str], model: str, effort: str | None) -> Run:
    with _current_lock:
        cur = CURRENT["run"]
        if cur is not None and cur.status == "running":
            raise RuntimeError("이미 생성 중이에요. 끝난 뒤 다시 실행해 주세요.")
        run = Run(scope, case_ids, model, effort)
        # 기록 전에 막을 상황이면 스레드를 띄우지 않고 바로 알린다.
        if not core.load_api_key():
            raise RuntimeError("API 키가 없어요. 레포 폴더의 .env 파일에 OPENAI_API_KEY를 넣어 주세요.")
        try:
            recorder.preflight(run.questions)
        except recorder.RecordError as e:
            raise RuntimeError(str(e)) from e
        run.status = "running"
        CURRENT["run"] = run
    threading.Thread(target=run.execute, daemon=True).start()
    return run


def list_results() -> list[dict]:
    out = []
    for p in sorted(core.RUNS_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"runId": d.get("runId"), "scope": d.get("scope"), "evalScore": d.get("evalScore"), "file": f"runs/{p.name}",
                    "finishedAt": d.get("finishedAt"), "durationSec": d.get("durationSec"), "cases": len(d.get("cases") or []),
                    "questionAvg": d.get("questionAvg"), "dimAvg": d.get("dimAvg"), "failCounts": d.get("failCounts"),
                    "ruleFails": d.get("ruleFails"), "model": d.get("model"), "effort": d.get("effort"), "head": d.get("head"),
                    "status": d.get("status")})
    out.sort(key=lambda x: x.get("finishedAt") or "")
    return out


def load_result(run_id: str | None = None) -> dict | None:
    if run_id:
        p = core.RUNS_DIR / f"{run_id}.json"
        if p.exists() and p.resolve().parent == core.RUNS_DIR.resolve():
            return json.loads(p.read_text(encoding="utf-8"))
        return None
    lst = list_results()
    return load_result(lst[-1]["runId"]) if lst else None


def estimate_seconds(n_cases: int) -> float | None:
    """지난 실행 시간으로 예상 시간을 잡는다. 기록이 없으면 기준선 실측값을 쓴다."""
    same = [r["durationSec"] for r in list_results() if r.get("cases") == n_cases and r.get("durationSec") and r.get("status") == "done"]
    if same:
        return round(sum(same) / len(same))
    b = core.load_baseline()
    if b.get("secPerCase"):
        workers = core.CFG.get("workers", 5)
        return round(b["secPerCase"] * max(1, -(-n_cases // workers)))
    return None
