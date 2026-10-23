"""결정적(자동) 검사. LLM 없이 출력 JSON만 보고 통과·실패를 판정한다.

- hard: 하나라도 실패하면 그 케이스는 '탈락'이다. 프롬프트 원문의 명시 규칙만 hard로 둔다.
- warnings: 규칙은 아니지만 사람이 볼 만한 신호.
- metrics: 비율·길이 같은 집계 지표. 채점이 아니라 비교용이다.
"""
from __future__ import annotations

import json
import re
import unicodedata

STAR_TERMS = re.compile(r"\bSTAR\b|\bSituation\b|\bTask\b|\bAction\b|\bResult\b", re.IGNORECASE)
TIP_LEAK = re.compile(r"STAR|평가\s*기준|채점|expectedEvidence|근거를\s*평가", re.IGNORECASE)
MULTI_ASK_HINT = re.compile(r"(그리고|또한|아울러|동시에)\s")
HANGUL = re.compile(r"[가-힣]")
LETTER = re.compile(r"[A-Za-z가-힣]")
ABSTRACT_TAIL = ("역량", "능력", "이해")
VALID_QTYPE = {"behavioral", "situational"}
VALID_MISSING = {"ExpectedEvidence", "Action", "Result", "Task", "Situation"}

# A00 내부 값 노출: 입력·출력 JSON의 키 이름과 영어 변수명. 질문·답변 팁에 나오면 그 케이스는 0점.
LEAK_KEYS = [
    "positionId", "categoryId", "itemId", "categoryName", "categoryDefinition", "itemName", "itemDefinition",
    "positionInfo", "positionName", "jobDescription", "requirements", "preferredQualifications", "categories",
    "careerMemory", "resumeInfo", "experience", "educations", "careers", "projects", "activities", "notes",
    "questionIntent", "questionType", "expectedSTAR", "expectedEvidence", "interviewBlueprint",
    "evaluationRubric", "answerTip", "behavioral", "situational", "excellent", "acceptable",
]
LEAK_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(LEAK_KEYS) + r"|item|category|blueprint|rubric)(?![A-Za-z])", re.IGNORECASE)
ID_RE = re.compile(r"\bP\d{2}(-C\d(-I\d)?)?\b")

# 경고: 혜린님 큐레이터 프롬프트(실무 면접 step_3·4)에서 가져온 문장 규칙
ABSTRACT_WORDS = ["예상치 못한", "구체적 사고 과정", "구체적인 사고 과정", "다양한 상황", "어려운 상황", "효과적으로", "적극적으로"]
DOUBLE_CLAUSE_RE = re.compile(r"(을|를)\s*위해[^?.,]{0,40}?(했을|하던|할)\s*때")
SIMILAR_VERBS = [("조정", "대응"), ("검토", "확인"), ("파악", "분석"), ("정리", "구체화")]
WORD_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def leak_hits(text: str, extra_values: list[str]) -> list[str]:
    t = nfc(text)
    hits = [m.group(0) for m in LEAK_RE.finditer(t)]
    hits += [m.group(0) for m in ID_RE.finditer(t)]
    hits += [v for v in extra_values if v and len(v) >= 3 and v in t]
    return sorted(set(hits))


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(WORD_RE.findall(nfc(a))), set(WORD_RE.findall(nfc(b)))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def nfc(s: str | None) -> str:
    return unicodedata.normalize("NFC", s or "")


def klen(s: str | None) -> int:
    """공백 포함 글자 수 (NFC 정규화 후)."""
    return len(nfc(s).strip())


def klen_nospace(s: str | None) -> int:
    return len(re.sub(r"\s", "", nfc(s)))


def hangul_ratio(s: str | None) -> float:
    letters = LETTER.findall(nfc(s))
    if not letters:
        return 0.0
    return len(HANGUL.findall(nfc(s))) / len(letters)


def parse_json(text: str):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


class CheckResult:
    def __init__(self):
        self.hard: list[dict] = []
        self.warnings: list[dict] = []
        self.metrics: dict = {}
        self.disqualified: bool = False

    def add(self, cid: str, name: str, ok: bool, detail: str = "") -> None:
        self.hard.append({"id": cid, "name": name, "ok": bool(ok), "detail": detail})

    def warn(self, cid: str, detail: str) -> None:
        self.warnings.append({"id": cid, "detail": detail})

    @property
    def passed(self) -> bool:
        return all(h["ok"] for h in self.hard)

    def failed_ids(self) -> list[str]:
        return [h["id"] for h in self.hard if not h["ok"]]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "disqualified": self.disqualified,
            "failed": self.failed_ids(),
            "hard": self.hard,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


# ---------------------------------------------------------------------------
# 입력 해석 (예외 케이스의 override 지원)
# ---------------------------------------------------------------------------
def resolve_assignments_input(case: dict, position: dict) -> tuple[dict, list[dict]]:
    position_info = case.get("positionInfoOverride") or position["positionInfo"]
    categories = case.get("categoriesOverride") or position["frozenCategories"]
    return position_info, categories


def item_index(categories: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for cat in categories:
        for it in cat["items"]:
            idx[it["itemId"]] = {"categoryId": cat["categoryId"], "itemName": it["itemName"]}
    return idx


def _contains_any(text: str, terms: list[str]) -> list[str]:
    t = nfc(text).lower()
    return [x for x in terms if nfc(x).lower() in t]


# ---------------------------------------------------------------------------
# ③ assignments
# ---------------------------------------------------------------------------
def check_assignments(text: str, case: dict, position: dict) -> CheckResult:
    c = CheckResult()
    obj, err = parse_json(text)
    c.add("A01", "JSON 파싱", obj is not None, err or "")
    if obj is None:
        return c
    position_info, categories = resolve_assignments_input(case, position)
    idx = item_index(categories)
    exp = case.get("expectations", {})

    # A00: 내부 값 노출 → 그 케이스 0점, 나머지 검사는 하지 않는다.
    id_values = [position_info.get("positionId", "")] + list(idx.keys()) + [v["categoryId"] for v in idx.values()]
    leaks = []
    for iv in (obj.get("interviews") or []) if isinstance(obj, dict) else []:
        if not isinstance(iv, dict):
            continue
        for field in ("question", "answerTip"):
            h = leak_hits(iv.get(field, ""), id_values)
            if h:
                leaks.append(f"{iv.get('itemId', '?')}.{field}:{h}")
    c.add("A00", "내부 값 노출 없음(있으면 그 케이스 0점)", not leaks, "; ".join(leaks))
    if leaks:
        c.disqualified = True
        c.metrics = {"leaks": leaks}
        return c
    exp_type = exp.get("expectedQuestionType", {})
    anchors = exp.get("anchors", {})
    forbidden = exp.get("forbiddenTerms", [])

    c.add("A14", "최상위 스키마 키 일치", isinstance(obj, dict) and set(obj.keys()) == {"positionId", "interviews"},
          f"keys={sorted(obj.keys()) if isinstance(obj, dict) else type(obj).__name__}")
    c.add("A02", "positionId 일치", obj.get("positionId") == position_info["positionId"],
          f"{obj.get('positionId')!r} vs {position_info['positionId']!r}")
    ivs = obj.get("interviews") if isinstance(obj.get("interviews"), list) else []
    c.add("A03", "interview 개수 = item 개수", len(ivs) == len(idx), f"{len(ivs)} vs {len(idx)}")
    ids = [iv.get("itemId") for iv in ivs if isinstance(iv, dict)]
    cat_ok = all(idx.get(iv.get("itemId"), {}).get("categoryId") == iv.get("categoryId") for iv in ivs if isinstance(iv, dict))
    c.add("A04", "itemId 전부 1회·categoryId 일치", sorted(ids) == sorted(idx.keys()) and cat_ok,
          f"ids={ids}")

    len_bad, single_bad, star_bad, type_bad, forb_bad, tip_bad, bp_bad, rb_bad, ko_bad, key_bad = ([] for _ in range(10))
    lengths: list[int] = []
    type_match: list[bool] = []
    anchor_hits: list[bool] = []
    multi_hint: list[str] = []
    questions: dict[str, str] = {}
    for iv in ivs:
        if not isinstance(iv, dict):
            key_bad.append("<non-object>")
            continue
        iid = iv.get("itemId", "?")
        q = nfc(iv.get("question", ""))
        questions[iid] = q
        n = klen(q)
        lengths.append(n)
        if not (50 <= n <= 150):
            len_bad.append(f"{iid}({n}자)")
        if q.count("?") > 1 or q.count("？") > 1:
            single_bad.append(iid)
        if STAR_TERMS.search(q):
            star_bad.append(iid)
        if MULTI_ASK_HINT.search(q):
            multi_hint.append(iid)
        if hangul_ratio(q) < 0.5:
            ko_bad.append(iid)
        hit = _contains_any(q, forbidden)
        if hit:
            forb_bad.append(f"{iid}:{hit}")
        if set(iv.keys()) != {"categoryId", "itemId", "interviewBlueprint", "question", "answerTip", "evaluationRubric"}:
            key_bad.append(f"{iid}:{sorted(iv.keys())}")
        bp = iv.get("interviewBlueprint") or {}
        qt = bp.get("questionType")
        if qt not in VALID_QTYPE:
            type_bad.append(f"{iid}:{qt!r}")
        want = exp_type.get(iid)
        if want and want != "any":
            type_match.append(qt == want)
        star = bp.get("expectedSTAR") or {}
        bp_complete = (
            bool(nfc(bp.get("questionIntent")).strip())
            and set(star.keys()) == {"situation", "task", "action", "result"}
            and all(nfc(star.get(k)).strip() for k in star)
            and isinstance(bp.get("expectedEvidence"), list)
            and len(bp.get("expectedEvidence")) > 0
        )
        if not bp_complete:
            bp_bad.append(iid)
        rb = iv.get("evaluationRubric") or {}
        rb_ok = set(rb.keys()) == {"excellent", "acceptable", "poor"} and all(
            isinstance((rb.get(k) or {}).get("criteria"), list) and len(rb[k]["criteria"]) > 0 for k in rb
        )
        if not rb_ok:
            rb_bad.append(iid)
        tip = nfc(iv.get("answerTip", ""))
        if klen(tip) > 30 or TIP_LEAK.search(tip):
            tip_bad.append(f"{iid}({klen(tip)}자)")
        a = anchors.get(iid) or []
        if a:
            anchor_hits.append(bool(_contains_any(q, a)))

    c.add("A05", "질문 길이 50~150자(공백 포함)", not len_bad, ", ".join(len_bad))
    c.add("A06", "질문은 하나(물음표 1개 이하)", not single_bad, ", ".join(single_bad))
    c.add("A07", "질문에 STAR 용어 없음", not star_bad, ", ".join(star_bad))
    c.add("A08", "questionType 값 유효", not type_bad, ", ".join(type_bad))
    c.add("A09", "금지 용어 없음", not forb_bad, ", ".join(forb_bad))
    c.add("A10", "answerTip 30자 이내·평가 용어 노출 없음", not tip_bad, ", ".join(tip_bad))
    c.add("A11", "Blueprint 완비(의도·STAR 4항·근거)", not bp_bad, ", ".join(bp_bad))
    c.add("A12", "Rubric 3단계 criteria 완비", not rb_bad, ", ".join(rb_bad))
    c.add("A13", "질문이 한국어", not ko_bad, ", ".join(ko_bad))
    c.add("A15", "interview 객체 키 일치", not key_bad, "; ".join(key_bad))
    if multi_hint:
        c.warn("W-multi", f"복합 질문 의심 연결어: {', '.join(multi_hint)}")
    for iid, q in questions.items():
        ab = [w for w in ABSTRACT_WORDS if w in q]
        if ab:
            c.warn("W-abstract", f"{iid} 추상 표현: {ab}")
        if DOUBLE_CLAUSE_RE.search(q):
            c.warn("W-clause", f"{iid} 부사절 겹침(~를 위해 ~했을 때)")
        sv = [f"{a}·{b}" for a, b in SIMILAR_VERBS if a in q and b in q]
        if sv:
            c.warn("W-verb", f"{iid} 비슷한 동사 반복: {sv}")
    qs = list(questions.items())
    for i in range(len(qs)):
        for j in range(i + 1, len(qs)):
            if _jaccard(qs[i][1], qs[j][1]) >= 0.45:
                c.warn("W-dup", f"{qs[i][0]}·{qs[j][0]} 질문 내용이 많이 겹침")

    c.metrics = {
        "question_count": len(ivs),
        "question_len_avg": round(sum(lengths) / len(lengths), 1) if lengths else None,
        "question_len_min": min(lengths) if lengths else None,
        "question_len_max": max(lengths) if lengths else None,
        "type_match_rate": round(sum(type_match) / len(type_match), 3) if type_match else None,
        "anchor_hit_rate": round(sum(anchor_hits) / len(anchor_hits), 3) if anchor_hits else None,
        "multi_ask_hint_count": len(multi_hint),
        "questions": questions,
    }
    return c


# ---------------------------------------------------------------------------
# ② evaluation-topics
# ---------------------------------------------------------------------------
def _theme_covered(theme: str, haystack: str) -> bool:
    tokens = [t for t in re.split(r"[^A-Za-z가-힣0-9]+", nfc(theme)) if len(t) >= 2]
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t.lower() in haystack)
    return hits * 2 >= len(tokens)


def check_topics(text: str, case: dict, position: dict) -> CheckResult:
    c = CheckResult()
    obj, err = parse_json(text)
    c.add("T01", "JSON 파싱", obj is not None, err or "")
    if obj is None:
        return c
    topics_input = case.get("inputOverride") or position["topicsInput"]
    exp = case.get("expectations") or position.get("topicsExpectations") or {}
    traps = exp.get("genericTraps", [])
    themes = exp.get("coreThemes", [])

    c.add("T07", "최상위 스키마 키 일치", isinstance(obj, dict) and set(obj.keys()) == {"positionName", "category"},
          f"keys={sorted(obj.keys()) if isinstance(obj, dict) else type(obj).__name__}")
    c.add("T02", "positionName 입력값 그대로", nfc(obj.get("positionName")) == nfc(topics_input["positionName"]),
          f"{obj.get('positionName')!r}")
    cats = obj.get("category") if isinstance(obj.get("category"), list) else []
    c.add("T03", "category 1~2개", 1 <= len(cats) <= 2, f"{len(cats)}개")

    per_cat_bad, name_bad, key_bad, empty_bad, trap_bad = [], [], [], [], []
    cat_names, item_names = [], []
    all_text = []
    for ci, cat in enumerate(cats):
        if not isinstance(cat, dict):
            key_bad.append(f"category[{ci}] non-object")
            continue
        if set(cat.keys()) != {"categoryName", "categoryDefinition", "items"}:
            key_bad.append(f"category[{ci}]:{sorted(cat.keys())}")
        cn, cd = nfc(cat.get("categoryName")), nfc(cat.get("categoryDefinition"))
        cat_names.append(cn)
        all_text += [cn, cd]
        if not cn.strip() or not cd.strip():
            empty_bad.append(f"category[{ci}]")
        if _contains_any(cn, traps):
            trap_bad.append(f"category[{ci}]:{cn}")
        if not (40 <= klen(cd) <= 180):
            c.warn("W-catdef", f"categoryDefinition 길이 {klen(cd)}자: {cn}")
        items = cat.get("items") if isinstance(cat.get("items"), list) else []
        if not (1 <= len(items) <= 2):
            per_cat_bad.append(f"{cn}:{len(items)}개")
        for ii, it in enumerate(items):
            if not isinstance(it, dict):
                key_bad.append(f"category[{ci}].items[{ii}] non-object")
                continue
            if set(it.keys()) != {"itemName", "itemDefinition"}:
                key_bad.append(f"items[{ci}.{ii}]:{sorted(it.keys())}")
            name, d = nfc(it.get("itemName")), nfc(it.get("itemDefinition"))
            item_names.append(name)
            all_text += [name, d]
            if not name.strip() or not d.strip():
                empty_bad.append(f"items[{ci}.{ii}]")
            stripped = re.sub(r"[\s.。]+$", "", name)
            if klen(name) > 30 or stripped.endswith(ABSTRACT_TAIL):
                name_bad.append(f"{name}({klen(name)}자)")
            if _contains_any(name, traps):
                trap_bad.append(f"item:{name}")
            if not (60 <= klen(d) <= 260):
                c.warn("W-itemdef", f"itemDefinition 길이 {klen(d)}자: {name}")

    c.add("T04", "category당 item 1~2개", not per_cat_bad, ", ".join(per_cat_bad))
    c.add("T05", "itemName 30자 이내·추상어로 끝나지 않음", not name_bad, ", ".join(name_bad))
    dup = len(cat_names) != len(set(cat_names)) or len(item_names) != len(set(item_names))
    c.add("T06", "이름·정의 비어 있지 않고 중복 없음", not empty_bad and not dup, ", ".join(empty_bad) + (" 중복" if dup else ""))
    c.add("T08", "category 객체 키 일치", not key_bad, "; ".join(key_bad))
    c.add("T09", "직무 공통 역량어를 category·item 이름으로 쓰지 않음", not trap_bad, ", ".join(trap_bad))
    if exp.get("requireKorean"):
        non_ko = [n for n in cat_names + item_names if hangul_ratio(n) < 0.5]
        c.add("T10", "명칭이 한국어", not non_ko, ", ".join(non_ko))

    hay = " ".join(all_text).lower()
    covered = [t for t in themes if _theme_covered(t, hay)]
    c.metrics = {
        "category_count": len(cats),
        "item_count": len(item_names),
        "core_theme_coverage": round(len(covered) / len(themes), 3) if themes else None,
        "covered_themes": covered,
        "category_names": cat_names,
        "item_names": item_names,
    }
    return c


# ---------------------------------------------------------------------------
# ④ answer-follow-up
# ---------------------------------------------------------------------------
def check_follow_up(text: str, case: dict, position: dict | None = None) -> CheckResult:  # noqa: ARG001
    c = CheckResult()
    obj, err = parse_json(text)
    c.add("F01", "JSON 파싱", obj is not None, err or "")
    if obj is None:
        return c
    inp = case["input"]
    exp = case.get("expectations", {})
    keys = {"positionId", "categoryId", "itemId", "isRelevantAnswer", "needFollowUp", "followUpReason", "followUpQuestion"}
    c.add("F08", "스키마 키 일치", isinstance(obj, dict) and set(obj.keys()) == keys,
          f"keys={sorted(obj.keys()) if isinstance(obj, dict) else type(obj).__name__}")
    c.add("F02", "positionId·categoryId·itemId 일치",
          all(obj.get(k) == inp.get(k) for k in ("positionId", "categoryId", "itemId")), "")
    rel, need = obj.get("isRelevantAnswer"), obj.get("needFollowUp")
    c.add("F03", "isRelevantAnswer·needFollowUp은 boolean", isinstance(rel, bool) and isinstance(need, bool),
          f"{rel!r}, {need!r}")
    reason, fq = obj.get("followUpReason"), obj.get("followUpQuestion")
    if rel is False:
        consistent = need is False and reason is None and fq is None
    elif need is True:
        consistent = isinstance(fq, str) and fq.strip() != "" and isinstance(reason, dict) and set(reason.keys()) == {"missingType", "target", "reason"}
    else:
        consistent = reason is None and fq is None
    c.add("F04", "플래그와 꼬리질문·이유의 일관성", consistent, f"need={need}, reason={type(reason).__name__}, fq={type(fq).__name__}")
    mt = (reason or {}).get("missingType") if isinstance(reason, dict) else None
    c.add("F05", "missingType 값 유효", (reason is None) or (mt in VALID_MISSING), f"{mt!r}")
    q = nfc(fq) if isinstance(fq, str) else ""
    q_ok = (not q) or (q.count("?") <= 1 and not STAR_TERMS.search(q) and hangul_ratio(q) >= 0.5)
    c.add("F06", "꼬리질문은 하나·STAR 용어 없음·한국어", q_ok, q[:80])
    exp_rel, exp_need = exp.get("expectedIsRelevant"), exp.get("expectedNeedFollowUp")
    dec_ok = (exp_rel is None or rel == exp_rel) and (exp_need is None or need == exp_need)
    c.add("F07", "판단이 기대값과 일치(관련성·꼬리질문 필요 여부)", dec_ok,
          f"rel {rel} vs {exp_rel}, need {need} vs {exp_need}")
    exp_mt = exp.get("expectedMissingType")
    forbidden = exp.get("forbiddenTerms", [])
    hit = _contains_any(q, forbidden) if q else []
    c.add("F09", "꼬리질문에 금지 용어 없음", not hit, str(hit))
    c.metrics = {
        "missing_type_match": (mt == exp_mt) if (exp_mt and mt) else None,
        "follow_up_len": klen(q) if q else None,
        "follow_up_question": q or None,
    }
    return c


CHECKERS = {
    "assignments": check_assignments,
    "evaluation_topics": check_topics,
    "answer_follow_up": check_follow_up,
}
