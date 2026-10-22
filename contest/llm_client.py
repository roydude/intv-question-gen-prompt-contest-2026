"""OpenAI 호출 클라이언트 (표준 라이브러리만 사용).

- Responses API(기본)와 Chat Completions API 둘 다 지원한다.
- gpt-6 계열은 temperature를 받지 않으므로 어떤 경우에도 보내지 않는다 (NJ-2624).
- 콘테스트에서는 같은 입력이라도 매번 새로 생성한다(캐시 없음). 생성 횟수를 정직하게 세기 위해서다.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, api_key: str | None, base_url: str = "https://api.openai.com/v1", api: str = "responses",
                 timeout: int = 240, max_retries: int = 3):
        self.api_key = api_key
        self.base_url = os.environ.get("OPENAI_BASE_URL", base_url).rstrip("/")
        self.api = api
        self.timeout = timeout
        self.max_retries = max_retries

    def complete(self, model: str, system: str, user: str, reasoning_effort: str | None = None,
                 json_mode: bool = True, max_output_tokens: int | None = None) -> dict:
        if not self.api_key:
            raise LLMError("API 키가 없어요. 화면의 시작 준비 카드나 설정 창에서 DM으로 받은 키를 저장해 주세요.")
        body, path = self._build(model, system, user, reasoning_effort, json_mode, max_output_tokens)
        t0 = time.time()
        resp = self._post(path, body)
        latency = int((time.time() - t0) * 1000)
        return {
            "text": self._extract_text(resp),
            "model": resp.get("model", model),
            "effort": reasoning_effort,
            "api": self.api,
            "usage": self._usage(resp),
            "response_id": resp.get("id"),
            "latency_ms": latency,
        }

    def check(self) -> int | None:
        """키로 모델 목록을 조회해 HTTP 상태 코드를 돌려준다. 네트워크 오류면 None. 비용이 들지 않는다."""
        req = urllib.request.Request(self.base_url + "/models", method="GET",
                                     headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except (urllib.error.URLError, TimeoutError, OSError):
            return None

    def ping(self) -> tuple[bool, str]:
        """키가 살아 있는지 확인한다."""
        if not self.api_key:
            return False, "키 없음"
        code = self.check()
        if code == 200:
            return True, "연결됨"
        if code is None:
            return False, "네트워크 오류"
        return False, "키 오류" if code in (401, 403) else f"HTTP {code}"

    # ---------- internals ----------
    def _build(self, model, system, user, effort, json_mode, max_output_tokens):
        if self.api == "responses":
            body = {"model": model, "input": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if effort and effort != "none":
                body["reasoning"] = {"effort": effort}
            if json_mode:
                body["text"] = {"format": {"type": "json_object"}}
            if max_output_tokens:
                body["max_output_tokens"] = max_output_tokens
            return body, "/responses"
        if self.api == "chat":
            body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if effort and effort != "none":
                body["reasoning_effort"] = effort
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            if max_output_tokens:
                body["max_completion_tokens"] = max_output_tokens
            return body, "/chat/completions"
        raise LLMError(f"지원하지 않는 api: {self.api}")

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:  # noqa: PERF203
                detail = e.read().decode("utf-8", errors="replace")[:1000]
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(2 ** attempt * 2)
                    last_err = LLMError(f"HTTP {e.code}: {detail}")
                    continue
                raise LLMError(f"HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise LLMError(f"네트워크 오류: {e}") from e
        raise LLMError(f"재시도 초과: {last_err}")

    def _extract_text(self, resp: dict) -> str:
        if self.api == "chat":
            try:
                return resp["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as e:
                raise LLMError(f"chat 응답 형식 오류: {json.dumps(resp)[:300]}") from e
        texts: list[str] = []
        for item in resp.get("output", []) or []:
            if item.get("type") != "message":
                continue
            for c in item.get("content", []) or []:
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
                elif c.get("type") == "refusal":
                    raise LLMError("모델이 거부했어요: " + c.get("refusal", ""))
        if not texts and resp.get("output_text"):
            texts.append(resp["output_text"])
        if not texts:
            status = resp.get("status")
            reason = (resp.get("incomplete_details") or {}).get("reason")
            raise LLMError(f"응답에 텍스트가 없어요 (status={status}, reason={reason})")
        return "".join(texts)

    @staticmethod
    def _usage(resp: dict) -> dict:
        u = resp.get("usage") or {}
        return {
            "input": u.get("input_tokens", u.get("prompt_tokens", 0)) or 0,
            "output": u.get("output_tokens", u.get("completion_tokens", 0)) or 0,
            "reasoning": ((u.get("output_tokens_details") or u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)) or 0,
        }


def estimate_cost_usd(usage: dict, model: str, prices: dict) -> float:
    p = prices.get(model)
    if not p:
        return 0.0
    return (usage.get("input", 0) * p["input"] + usage.get("output", 0) * p["output"]) / 1_000_000
