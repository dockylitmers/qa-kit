# 결함 카드 (8단계, GitHub 이슈 초안 — 영어)

한 카드에 결함 하나. 사실과 추정을 나눈다. 코드는 고치지 않는다.

```markdown
### <Symptom-first unique summary, e.g. "Late approval webhook after order expiry leaves charge without order">

**Severity:** critical | high | medium | low — <one-line reason>
**Evidence level:** L0 code | L1 failing test | L2 property | L3 fuzz | L4 fault injection | L5 mutation | LD device
**Area:** <web | frontend | server | mobile | infra — one or more, comma-separated>
**Suggested assignee:** <filled by `qa report assign` from projects/<p>/project.yaml owners; blank if there is no table — ask the lead>
**Environment / version:** <branch or commit>, <local|dev|staging>, frequency <always|intermittent n/m>
**Assumptions (preconditions):** <what must be true for this to happen — e.g. which webhook is registered in production; mark verified vs. assumed>
**Realistic frequency:** <how often in normal operation and why — window size, timeouts, call order>
**Patterns:** <PAT-ids or "out-of-pattern">
**Related:** <#n existing issues> · **Existing decision:** <#n closed as not planned on YYYY-MM-DD, or "none found" + the search used>

<!-- LD only: the three reproduction cells. Only "same" is a reproduction; otherwise use L0 or the confirmation list. -->
**Original action · end state:** <the report's own steps and where the screen ended, as reported>
**My action · end state:** <platform, device, my steps and where the screen ended, n/m>
**Match:** same | similar symptom | different

#### Steps to reproduce
1. …
2. …

#### Expected (source: <spec / acceptance criterion / provider doc>)
…

#### Actual
…

#### Propagation
trigger → <where it is caught or swallowed> → <what retries or compensates> → visible failure → **state left behind** (rows, money, external state)

#### Evidence
- `<file>:<line>` — <what this line shows>
- failing test: `<path in evidence bundle>`, command `<cmd>`, output excerpt
- device (LD): observed values in text (e.g. computed colour, `:hover` state, response field), URL, step order and count — a screenshot file name alone is not evidence; say how the image gets attached (e.g. dragged in on the web by the person who posts)
- refutation round 1 (in run): <verdict, severity — what was checked and why it did not hold>
- refutation round 2 (fresh context, before reporting): <verdict, severity — facts corrected>

#### Suspected cause (inference) and how to confirm
…

#### Suggested regression test
…
```

## 좋은 카드 / 나쁜 카드

- 좋음: "Duplicate approval webhook fulfils the order twice when the first delivery times out" — 증상, 조건, 결과가 제목에 있다.
- 나쁨: "Webhook bug", "Possible race condition" — 무엇이 깨지는지 모른다.
- 나쁨: 증거 없이 "could potentially lead to…"만 있는 카드 → 확인 요청 목록으로 보낸다.
- 나쁨: "if the configuration is wrong, every order is affected" 같은 증폭 문장을, 앞 단계가 같은 검사를 먼저 해서 그 경우를 막는지 확인하지 않고 쓴 카드.
- 나쁨: 원문과 다른 조작·다른 플랫폼에서 본 비슷한 증상을 `Match: same`으로 쓴 카드. 원인이 같아도 끝 상태가 다르면 `similar symptom`이다.
- 나쁨: 요청서에 적힌 담당을 그대로 옮긴 `Suggested assignee`. 담당은 프로젝트 담당 표에서 온다.
- 명세가 지금 동작을 정한 화면 문구·순서·배치 요청은 결함 카드가 아니라 변경 요청 목록으로 보낸다(명세 항목 줄 인용).
- 기존 결정과 같은 문제(닫힌 not planned 이슈, 기록된 won't-fix)는 새 카드 대신 "기존 결정 충돌"로 표시하고 그 이슈용 코멘트 초안만 쓴다.

## 확인 요청 목록 항목

```markdown
- [ ] <hypothesis sentence> — evidence so far: <file:line>; what would confirm: <test/experiment>; why not proven: <time/env/access>
```

## 반박된 가설 항목

```markdown
- ~~<hypothesis>~~ — refuted by <file:line | spec | run output>: <one line>. Trap candidate: yes/no
```
