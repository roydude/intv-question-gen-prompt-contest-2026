# 골든셋

연습과 공식 평가에 똑같이 쓰는 공통 케이스 10개예요. 모두 공개예요.

| 파일 | 내용 |
|---|---|
| `cases_common.json` | 케이스 10개. 지원자 경험(커리어 메모리·이력서 요약), 케이스마다 고정된 평가 주제 1개·평가 요소 2개(`categoriesOverride`), 채점에 쓰는 기대값(`expectations`) |
| `positions.json` | 케이스가 쓰는 포지션 10개의 공고 정보(`positionInfo`) |
| `rubrics.json` | AI 채점 루브릭 J1~J4와 실패 유형 C1~C5 |

③ 프롬프트에는 `positionInfo`, `categories`(= `categoriesOverride`), `experience`가 JSON으로 들어가요. 화면의 **공고·지원자 정보** 창에서 같은 내용을 볼 수 있어요.

`expectations`의 참고 질문(`referenceQuestions`)·앵커·심사 메모는 AI 채점관이 참고하는 값이에요. 이 값을 프롬프트에 옮겨 적거나 특정 케이스만 맞추는 규칙을 넣는 것은 지양해요. 우승은 상위 3명의 질문을 블라인드로 비교해 정해요.
