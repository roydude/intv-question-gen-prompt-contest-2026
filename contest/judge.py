"""AI 채점(judge) 프롬프트 생성과 결과 파싱.

케이스 하나의 ③ 출력을 루브릭 J1~J4 차원별 1~5점으로 매긴다. 공식 평가도 같은 프롬프트·모델을 쓴다.
"""
from __future__ import annotations

import json
import re

from .checks import parse_json, resolve_assignments_input

JUDGE_SYSTEM = """당신은 채용 면접 질문 품질을 심사하는 심사위원이다. 입력으로 공고, 평가 요소, 지원자 경험, 생성된 질문(및 설계도·채점 기준)과 심사 루브릭을 받는다.

원칙
- 루브릭의 차원마다 1~5점을 매기고 한 문장으로 이유를 적는다. 점수는 보수적으로 준다. 근거 없는 5점을 주지 않는다.
- '참고 질문'은 작성자가 쓴 예시일 뿐 정답이 아니다. 참고 질문과 다르다는 이유로 감점하지 않는다. 참고 질문이 지키는 조건(경험 소재, 유형, 단일 의도)을 기준으로 삼는다.
- '심사 메모'가 지정한 감점 사유는 반드시 확인한다.
- 입력에 없는 사실(수치·역할·회사)을 전제한 질문은 J3에서 1~2점이다.
- 지원자에게 답하라고 요구하는 행동이 2개 이상 묶여 있으면(예: "~을 정하고 ~을 확인한 과정") J2는 3점 이하다. 장면을 여는 절 하나("~할 때", "~한다면")의 동사는 동작으로 세지 않는다. 장면을 여는 절이 2개 이상이거나 목적절과 조건절이 겹치면("~를 위해 ~했을 때") 3점 이하다.
- J2(질문 명확성)는 루브릭의 실패 유형 C1~C5로 판정한다. 해당 없음 5점, 하나 3점, 둘 이상 1점이다. reason에는 걸린 코드를 먼저 적는다(예: "C2: 답할 대상이 '어떻게'뿐"). 걸린 것이 없으면 "해당 없음"이라고 적는다.
- 경험이 적은 신입에게 수업 과제·아르바이트를 짚어 묻는 것은 허용한다. 단 실제로 한 일의 범위를 넘어 실무처럼 부풀리면 J3 감점.
- 같은 지원자에게 나간 질문끼리 주제·평가 목적이 겹치면 J1 감점.
- 출력은 JSON 하나만 낸다. 설명·마크다운을 붙이지 않는다."""

FAIL_CODE_RE = re.compile(r"\bC[1-5]\b")


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _rubric_block(rubric_stage: dict) -> str:
    lines = []
    for d in rubric_stage["dimensions"]:
        lines.append(f"- {d['id']} {d['name']} (가중치 {d['weight']}): {d['question']}")
        for k in ("5", "3", "1"):
            if k in d.get("anchors", {}):
                lines.append(f"    {k}점: {d['anchors'][k]}")
    return "\n".join(lines)


def _experience_summary(case: dict) -> str:
    exp = case.get("experience") or {}
    cm = exp.get("careerMemory") or {}
    parts = []
    for k in ("careers", "projects", "activities", "educations", "licenses", "languageExams", "languageSkills", "skills", "notes"):
        if k in cm:
            parts.append(f"{k}: {_dump(cm[k])}")
    if exp.get("resumeInfo"):
        parts.append("resumeInfo: " + exp["resumeInfo"])
    return "\n".join(parts) if parts else "(경험 입력 없음)"


def build_judge(case: dict, position: dict, output_text: str, rubrics: dict) -> tuple[str, str]:
    rubric_stage = rubrics["stages"]["assignments"]
    out_obj, _ = parse_json(output_text)
    out_str = _dump(out_obj) if out_obj is not None else output_text
    exp = case.get("expectations", {})
    position_info, categories = resolve_assignments_input(case, position)
    schema = {
        "items": [
            {"itemId": "string", "scores": {d["id"]: {"score": "1~5 정수", "reason": "한 문장"} for d in rubric_stage["dimensions"]},
             "issues": ["짧은 문제 지적 0~3개"]}
        ],
        "caseComment": "케이스 전체 한 줄 총평",
    }
    user = f"""## 공고
{_dump(position_info)}

## 평가 주제와 요소 (이 item마다 질문 하나가 나와야 한다)
{_dump(categories)}

## 지원자 경험 입력
{_experience_summary(case)}

## 케이스 정보
- 시나리오: {case.get('scenario')} {case.get('scenarioLabel', '')}
- 페르소나: {case.get('personaSummary', '')}
- item별 기대 질문 유형: {_dump(exp.get('expectedQuestionType', {}))}
- item별 경험 앵커(이 중 하나라도 짚으면 경험을 활용한 것): {_dump(exp.get('anchors', {}))}
- 금지 용어: {_dump(exp.get('forbiddenTerms', []))}
- 참고 질문(정답 아님): {_dump(exp.get('referenceQuestions', {}))}
- 심사 메모: {exp.get('judgeNotes', '')}

## 심사할 출력 (프롬프트가 생성한 결과)
{out_str}

## 루브릭
{_rubric_block(rubric_stage)}

## 출력 형식 (JSON만)
{_dump(schema)}
item 순서와 itemId는 심사할 출력의 interviews 순서를 따른다."""
    return JUDGE_SYSTEM, user


def dim_points(score, weight: float) -> float | None:
    """1~5점을 가중치 점수로 바꾼다. 5점=가중치 전부, 3점=절반, 1점=0."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    s = min(5.0, max(1.0, s))
    return round(weight * (s - 1) / 4, 2)


def parse_judge(text: str, rubrics: dict) -> dict:
    rubric_stage = rubrics["stages"]["assignments"]
    obj, err = parse_json(text)
    if obj is None:
        return {"ok": False, "error": err}
    dims = rubric_stage["dimensions"]
    items = []
    for it in obj.get("items") or []:
        sc = it.get("scores") or {}
        points, total, ok = {}, 0.0, True
        for d in dims:
            p = dim_points((sc.get(d["id"]) or {}).get("score"), d["weight"])
            points[d["id"]] = p
            if p is None:
                ok = False
            else:
                total += p
        j2_reason = str((sc.get("J2") or {}).get("reason", ""))
        codes = sorted(set(FAIL_CODE_RE.findall(j2_reason)))
        items.append({
            "itemId": it.get("itemId"),
            "scores": sc,
            "points": points,
            "total": round(total, 1) if ok else None,
            "failCodes": codes,
            "issues": it.get("issues") or [],
        })
    return {"ok": True, "items": items, "comment": obj.get("caseComment", "")}
