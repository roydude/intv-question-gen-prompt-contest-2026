---
name: 시작
description: 콘테스트 처음 설정. 실행 환경을 점검하고 .env를 준비하고 개인 브랜치(contest/영문이름)를 만든다. 참가자가 /시작을 입력했을 때 쓴다.
---

# /시작

참가자가 처음 레포를 받았을 때 한 번 한다. 이미 끝난 단계는 건너뛴다. 질문은 한 번에 하나만 한다.

1. `uv --version`으로 uv를 확인한다. 없으면 설치 명령을 알려 주고 참가자가 직접 설치하게 한다.
   - macOS: `brew install uv` 또는 `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows(PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. `uv run app.py doctor`로 상태를 본다.
3. git 사용자 이름·이메일이 없으면 참가자에게 물어 `uv run app.py setup --git-name "이름" --git-email "메일"`을 실행한다.
4. 브랜치가 `contest/`로 시작하지 않으면 영문 이름(소문자·숫자·하이픈)을 물어 `uv run app.py setup --name <영문이름>`을 실행한다. 개인 브랜치를 만들고 원격에 올린다.
5. API 키가 비어 있으면 이렇게 안내한다. "레포 폴더의 .env 파일을 편집기(메모장, VS Code 등)로 열어 `OPENAI_API_KEY=` 뒤에 DM으로 받은 키를 붙여 넣고 저장하세요. 키를 이 대화창에 붙여 넣지 마세요." .env는 직접 열거나 고치지 않는다. 참가자가 넣었다고 하면 `uv run app.py doctor --ping`으로 연결을 확인한다.
6. 화면을 켜는 방법을 알려 준다. "새 터미널 창에서 이 폴더로 이동해 `uv run app.py`를 실행하면 브라우저에 http://localhost:8000 이 열려요. 끌 때는 그 터미널에서 Ctrl+C." Claude Code 안에서 서버를 직접 띄우지 않는다(대화가 멈춘다).
7. 규칙을 세 줄로 정리해 준다.
   - 한 사이클은 공통 케이스 10개 × 평가 요소 2개 = 질문 20개, 1인 상한은 질문 400개다.
   - 생성할 때마다 실행 기록이 runs/에 남고 개인 브랜치에 자동으로 커밋·푸시된다. 기록은 고치지 않는다.
   - 마감 전에 `/제출`로 PR을 열고, 마감 시점의 마지막 커밋이 제출본이다.
