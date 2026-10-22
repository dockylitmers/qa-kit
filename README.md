# qa-kit

장애 중심 QA 에이전트 키트. Claude Code에서 **실패 경계·새드 패스·에러 전파 경로**를 찾아 증거와 함께 보고하고, 프로젝트를 거칠수록 지식 저장소(`qa-knowledge`, 비공개)가 자란다.

| 구성 | 하는 일 |
|---|---|
| `skills/qa-run` | 절차: 0 모드 선택(코드만·기기만·둘 다, 대화형에서만 묻고 헤드리스는 코드) + 안전 계약 + 커버리지 표(스크립트가 만든 진입점 × 점검 축) → 1 정찰 → 2 기능 재설계(장애 설계 카드) → 3 위험 분석 → 4 전파 추적 → 5 가설 순위 → 6 증명 L0–L5·LD(기기 재현) → 7 반박 검증 두 번(실행 중 건별 + 보고 직전 새 컨텍스트 건별) → 8 커버리지 관문(빈칸이면 보고 불통과) + 기존 이슈·결정 검색 + 결함 카드 보고 → 9 회고 기록 |
| `skills/device-qa` | 기기 재현 도구(qa-run 6단계 `LD`): iOS 시뮬레이터·Android 에뮬레이터를 잠금·실제 터치 판정·캡처·녹화·권한 팝업 처리로 조작(`scripts/device_qa.py`), 기기 경계 대입표, 실기기는 Appium 직접 |
| `skills/qa-retro` | 회고(수동 전용): 판정·반박된 가설·놓친 결함(`misses`)에서 패턴 변경을 항목 단위로 `proposals/`에 제안 |
| `agents/qa-breaker` | 공격·증명(1–6단계). 외부 발송 도구 없음, 지식 저장소에 쓰지 않음, `qa-run` 미리 로드. 후보마다 환경 가정·현실 빈도·심각도 근거 필수 |
| `agents/qa-skeptic` | 반박 검증(7단계). **결함 하나당 한 번** 불러 "사실이 아님"을 증명하려고 시도하고 빈도·선행 조건으로 심각도를 다시 잰다 |
| `bin/qa` | CLI(Python 표준 라이브러리만): 설치·대상 레포 환경 검사, 형식 검사, 패턴, 실행 기록·반박·판정·통계, 심각도 보정, 제안 반영, 기존 이슈 검색, 놓친 결함 수집, 재현 평가, 놓침 검사(커버리지 표·심은 결함·블라인드 재도출), 동기화 |
| `install.sh` | 사용자 범위 설치(심볼릭 링크 + `~/.claude/settings.json`의 `env.QA_KB`) |

MCP 서버는 없다. 지식은 git 파일로 쌓고, LLM은 내용만 쓰고, 형식 검사·병합·통계는 CLI가 한다.

## 새 컴퓨터 설치 (5분)

```sh
gh auth login                                              # GitHub 계정 dockylitmers
gh repo clone dockylitmers/qa-kit ~/Workspace/qa-kit
bash ~/Workspace/qa-kit/install.sh                         # 지식 저장소가 없으면 ~/Workspace/qa-knowledge로 클론
qa doctor                                                  # 모든 항목 ok, 종료 코드 0
```

- `~/.local/bin`이 PATH에 없으면 설치 스크립트가 알려 준다(`export PATH="$HOME/.local/bin:$PATH"`).
- 지식 저장소 위치를 바꾸려면 `bash install.sh --kb /path/to/qa-knowledge`.
- 설치 뒤 **새 Claude Code 세션**을 열어야 `QA_KB`가 들어간다.
- 여러 번 실행해도 안전하다. `settings.json`은 바꾸기 전에 `settings.json.bak-qa-kit-<시각>`으로 백업하고 `env.QA_KB` 한 키만 더한다.
- 갱신: `git -C ~/Workspace/qa-kit pull` (링크라서 다시 설치할 필요 없음. 새 스킬·에이전트가 생겼을 때만 `install.sh`).

설치되는 것(모두 사용자 범위):

```
~/.claude/skills/qa-run, qa-retro, device-qa   -> qa-kit/skills/...
~/.agents/skills/qa-run, qa-retro, device-qa   -> qa-kit/skills/...   (Codex 등 같은 SKILL.md 형식을 읽는 도구용)
~/.claude/agents/qa-breaker.md, qa-skeptic.md -> qa-kit/agents/...
~/.local/bin/qa                     -> qa-kit/bin/qa
~/.claude/settings.json             env.QA_KB = <지식 저장소 경로>
```

## 쓰는 법

### 실행

Claude Code 세션에서:

```
/qa-run 결제 승인 → 발급 → 환불 사슬, paths: <경로들> project=<project> run=2026-10-21-payments
```

- 호출한 세션이 안전 계약(범위·환경·중단 조건)을 쓰고(`qa doctor --target <레포>`로 그 기기에서 할 수 있는 증명 등급을 먼저 확인), 1–6단계는 `qa-breaker`, 7단계는 `qa-skeptic`(1차: 실행 중 건별, 2차: 보고 직전 새 컨텍스트 건별)에게 맡기고, 기존 이슈·결정을 검색한 뒤(`qa issue search`) 결함 카드(영어 이슈 초안)와 반박된 가설 목록을 만든다.
- 시작하면 무엇을 할지 묻는다: **코드만**(코드·명세로 가설, 실패 테스트로 증명), **기기만**(기기 화면에서 앱·기기 경계를 흔들어 재현), **둘 다**. 인자 `mode=code|device|both`를 주면 묻지 않는다. 기기 모드는 `python3 -I ~/.claude/skills/device-qa/scripts/device_qa.py doctor --platform all`이 통과해야 한다(설치: `skills/device-qa/references/setup.md`).
- 산출물은 고객 레포 밖 실행 폴더 `${QA_WORK:-~/.qa-work}/<project>/<run-id>/`에 쓴다.
- 이슈 게시·라벨·메시지는 하지 않는다. 사람이 승인 묶음으로 한다.

헤드리스(로컬 cron·launchd):

```sh
cd <project repo> && claude -p --agent qa-breaker \
  --append-system-prompt-file ~/.claude/skills/qa-run/SKILL.md \
  --add-dir="$HOME/.claude/skills/qa-run" "<scope>"
```

헤드리스 실행은 모드를 묻지 않고 범위 파일의 `mode`(없으면 `code`)로 진행한다.

`--agent`로 메인 스레드에서 돌릴 때는 에이전트의 `skills:` 미리 로드가 적용되지 않아(서브에이전트로 띄울 때만 적용) 절차를 직접 붙인다. `qa eval run`은 이렇게 한다.

### 기록과 판정

```sh
qa init <project> [--repo ~/path/to/clone]                 # 프로젝트 폴더 만들기(한 번)
qa finding applied --project <p> --run <run> --patterns PAT-a,PAT-b
qa finding add --project <p> --run <run> --title "..." --severity high --evidence L1 \
   --class error_propagation --location "file:line" --patterns PAT-a \
   --assumption "webhook registered in production" --frequency "rare: ms window" \
   --severity-basis "money moves; needs two failures to line up"           # 세 칸 필수
qa finding add … --evidence LD --repro-original "<신고: 조작·끝 상태>" \
   --repro-mine "<내 조작·끝 상태, 플랫폼, n/m>" --repro-match same       # LD는 세 칸 필수, same만 LD
qa finding review --project <p> --run <run> --finding F001 --round 1|2 \
   --verdict stands|downgraded|refuted|uncertain --severity medium --reason "..." [--corrections "..."]
qa finding judge --project <p> --run <run> --finding F001 --verdict accepted \
   --reason "fixed in next release" --severity high --by <who> --issue 999 [--related 405,406]
qa finding list --project <p> [--run <run>]                # 처음 심각도, 1차·2차 반박, 판정, 이슈
qa calibration add --project <p> --run <run> --finding F001 --reason "<client-neutral>" --tags ms_window
qa calibration list                                        # 처음 → 1차 → 2차 → 확정, 하향 비율
```

- 보고 전 결함 카드 검사·담당 채우기(`report.md`):

  ```sh
  qa report assign <run>/report.md --project <p>   # 카드의 **Area:** → project.yaml owners → **Suggested assignee:**
  qa report check <run>/report.md --project <p>    # LD 카드의 재현 세 칸·Match same, 담당이 표와 같은지(오류면 종료 코드 1)
  qa report owners --project <p> --area web,server # 담당 표 조회
  ```

  담당 표는 `projects/<p>/project.yaml`에 둔다(`owners: {web: [login], frontend: [login], server: [login]}`, 제안 `project-set`으로 넣는다). 표가 없으면 담당 칸은 비우고 리드에게 묻는다.
- 판정(`accepted | false_positive | wont_fix | duplicate`)이 들어오면 CLI가 쓰인 패턴의 `stats`를 실행 기록에서 다시 계산한다(LLM이 카운터를 만지지 않는다).
- `--issue`는 그 결함이 게시된 이슈 번호다. `qa misses collect`가 키트가 낸 이슈를 빼는 근거이므로 게시 뒤 꼭 적는다.
- 심각도 보정(`calibration/severity.jsonl`)은 고객사 이름 없이 쓴다(`qa check`가 공통 패턴과 같은 금지어 검사를 한다). 행은 실행을 가리키는 불투명 해시만 갖는다.

### 회고

```
/qa-retro <project> <run-id>
/qa-retro misses <project> [since=YYYY-MM-DD]
/qa-retro <project> missed=<고객사 QA나 운영에서 나온 결함 설명>
```

변경은 `proposals/`에 항목 단위로 제안만 한다. 반영할 수 있는 항목은 `qa-apply` 블록(형식은 `skills/qa-retro/SKILL.md` 5절)을 갖고, 리드 승인 뒤 CLI가 결정적으로 반영한다:

```sh
qa proposal show <file>                                    # 항목·결정·반영 상태
qa proposal decide <file> --approve P1,P2 --reject P3 --by <lead>
qa proposal apply <file> [--dry-run]                       # 새 패턴(항상 candidate)·필드 수정·통계 귀속·평가 연결
                                                           # → stats·INDEX 갱신 → qa check, 오류면 모든 파일 되돌림
qa sync
```

새 패턴은 `candidate`로만 들어간다. 평가를 통과한 뒤 별도 제안(`set status active`)으로 올린다.

### 기존 이슈·결정 검색과 놓친 결함

이슈 트래커는 **읽기만** 한다. 프로젝트마다 `projects/<p>/project.yaml`에 둔다:

```yaml
issue_tracker: {kind: github, repo: owner/name}            # gh issue list (읽기)
# Jira 등: JSON 배열(number,title,labels,state,stateReason,createdAt)을 출력하는 명령. 셸 없이 실행한다.
# issue_tracker: {kind: command, list: "my-jira list --since {since} --label {label}", search: "my-jira search {query}"}
# area_labels: 영역으로 볼 라벨만 고르려면 issue_tracker 안에 [backend, frontend, mobile]
owners: {web: [login-a], frontend: [login-a], server: [login-b]}   # 결함 카드 Suggested assignee의 출처(qa report assign)
```

```sh
qa issue search --project <p> <핵심어>        # 닫힌 것까지(not planned 표시) + 기록된 won't-fix 판정·정책
qa misses collect --project <p> --since YYYY-MM-DD   # → projects/<p>/misses/<오늘>.md
```

`misses collect`는 그 날짜 이후 생긴 `bug` 이슈에서 실행 기록에 이슈 번호가 있는 것(키트가 낸 것)을 빼고, 번호·제목·라벨·영역 표와 건별 분류 칸을 만든다. 같은 날 파일이 있으면 덮어쓰지 않는다(`--force`).

**운영 절차 — 고객사 QA 기간 주 2회(월·목):**

1. **collect** — `qa misses collect --project <p> --since <지난 수집일>` (첫 회차는 키트 실행 시작일).
2. **분류** — `/qa-retro misses <p>`: 건마다 키트 범위였나(실패 경계형인가), 그 영역을 실행했나, 놓친 이유(모델에 없던 진입점 / 빠진 안내어 / 증명 실패 / 판정 오류), 고칠 패턴·절차, 평가 사례 후보.
3. **제안** — 고칠 곳을 `proposals/<날짜>-misses-<p>.md`에 항목 단위로(`qa-apply` 블록 포함). 절차 변경은 키트 저장소 변경 제안으로 따로.
4. **리드 검토** — 리드가 `qa proposal decide` → `qa proposal apply` → `qa sync`. 평가 사례 후보는 고친 커밋이 생기면 `evals/<p>/`에 넣는다.

### 패턴

```sh
qa pattern list [--status active] [--tier global] [--class concurrency] [--json]
qa pattern show PAT-<id>
qa pattern new PAT-<slug> --title "..." --class boundary --severity high [--project <p>]
qa pattern index                                           # INDEX.md 다시 만들기(패턴당 한 줄)
```

### 재현 평가

```sh
qa eval list [--project <p>]
qa eval run <case> [--repo <clone>] [--timeout 30] [--max-budget 5] [--keep] [--dry-run] [--repeat k] [--no-patterns]
qa eval report [--project <p>] [--out report.md]   # 사례별 · 패턴 켬/끔별 재현율 표
qa eval audit [--project <p>]                      # 사례 linked_patterns 점검(leave-one-out에서 숨길 패턴)
```

- `--repeat k`: 같은 사례를 k번(매번 새 스냅샷·새 패턴 복사본). 결과 파일은 `<case>-<시각>-r<i>.md`. 에이전트 출력은 실행마다 달라서 재현율은 여러 번 돌려 본다.
- `report`: 결과 파일의 `Verdict:` 줄(사람 채점)과 `pattern mode` 행으로 사례 × 켬/끔 표를 만든다. recall = (hit + 0.5 × partial) / 채점한 수. 무효 실행은 빼고 따로 센다.
- `audit`: 사례마다 숨길 패턴을 보여 준다. 패턴의 실제 사례·oracle이 그 사례의 결함을 그대로 말하면 `linked_patterns`에 넣어야 한다(아니면 패턴 라이브러리가 답을 알려 준다). 공통 패턴은 사례 ID를 가질 수 없으므로(금지어) 연결은 사례 쪽에만 적는다.

- 사례의 `pre_fix_commit`을 `git archive`로 이력 없이 `~/Workspace/.qa-eval/w-<무작위>/`에 풀고 `git init`(미래 커밋·이슈 번호가 보이지 않게). 폴더 이름은 사례 ID 대신 무작위 값을 쓴다(사례 ID에 이슈 번호·증상이 들어 있어 에이전트가 작업 폴더 이름으로 답을 볼 수 있음). 사례와 폴더의 대응은 결과 파일에 적힌다.
- 스냅샷 안의 프로젝트 설정·훅은 읽지 않는다(`--setting-sources user`).
- **격리:** 에이전트는 지식 저장소를 보지 않는다. 실행마다 공통 패턴을 `~/Workspace/.qa-eval/p-<무작위>/`(mini-KB: `patterns/`, `INDEX.md`)에 복사하고 그 경로만 준다. 그 실행 동안 `QA_KB`도 이 복사본을 가리킨다. 사례의 `linked_patterns`와 `eval_links`로 그 사례를 가리키는 패턴은 숨긴다(leave-one-out). 숨긴 목록은 결과 파일에 적힌다.
- **transcript 검사:** 전체 stream-json 기록을 `~/Workspace/.qa-eval/transcripts/`(로컬, 커밋 안 함)에 남기고, `qa-knowledge`·지식 저장소 경로·`evals/<project>`·사례 파일 이름·사례 ID·숨긴 패턴 ID가 나오면 결과를 **INVALID**로 표시하고 종료 코드 1. `qa eval list`는 유효·무효 실행을 따로 센다.
- `--no-patterns`: 패턴 없이 돌리는 기준선(학습 효과 비교용).
- `claude -p --agent qa-breaker`에 **범위(scope)만** 준다. 이슈 본문·번호·`expected_finding`은 프롬프트에 넣지 않는다. 도구는 Read·Grep·Glob만(읽기 전용).
- 결과는 `evals/<project>/results/<case>-<시각>.md`. v0 채점은 기대(위치·증상)와 에이전트 출력을 나란히 두고 사람이 표시한다.
- 레포 위치: `--repo`, `QA_REPO_<PROJECT>` 환경 변수, 또는 `projects/<project>/project.yaml`의 `repo_paths`.

### 놓침 검사(재현율)

반박(`qa-skeptic`)은 찾은 결함이 맞는지(정밀도)만 본다. 놓친 것은 세 장치로 본다.

**1. 커버리지 표 — 매 실행, 보고 관문**

```sh
qa coverage init --project <p> --run <run> --repo <레포> --scope <경로 …> [--invariants <실행 폴더>/model.md]
qa coverage set  --project <p> --run <run> --rows E012,E020-E031 --axis conc,dup --checked "H3 evidence/H3/"
qa coverage set  --project <p> --run <run> --group "reads" --only-blank --out-of-scope "읽기 전용, 이번 범위는 쓰기"
qa coverage check --project <p> --run <run> [--json]     # 빈칸·형식 오류가 있으면 종료 코드 1
```

- 표 파일: `${QA_WORK:-~/.qa-work}/<project>/<run>/coverage.md`(고객 레포 밖, `--file`로 바꿀 수 있음). 레포는 읽기만 한다.
- 행은 코드에서 기계적으로 뽑는다: NestJS `@Controller` 접두사 + `@Get/@Post/@Put/@Patch/@Delete`(경로 상수는 레포 안 `const`에서 풂), 권한 데코레이터·가드(`@Permissions`, `@Roles`, `@UseGuards(…)`, `@Public`), 웹훅(경로·이름에 webhook·callback), `@Cron/@Interval/@Timeout`·`setInterval`·cron 라이브러리, `@OnEvent`, 큐(`@Processor`/`@Process`, `@EventPattern` …), Next.js `app/**/route.ts`·`pages/api`·`'use server'` 파일. 앱·화면 진입점: `screen`(Expo Router 파일 라우트 — `_layout` 제외, `(그룹)` 제거, `[param]` 표기, `useLocalSearchParams`로 읽는 값은 `?param`; Next.js `app/**/page.*`·`pages/**`), `deeplink`(`app.json`·`app.config`·Info.plist·AndroidManifest의 스킴 × 화면 경로, 맞는 화면 없는 경로, `associatedDomains`·`intentFilters`, `Linking.addEventListener('url')`·`getInitialURL`·`useURL`), `push`(알림 응답 처리기 → 그 화면과 페이로드에서 읽는 목적지 필드 — 한 번 import한 파일까지; 수신 처리기), `external`(`Linking.openURL`·`WebBrowser`·`IntentLauncher`, WebView `onShouldStartLoadWithRequest`·`onMessage`·`onOpenWindow`, `AppState` 복귀). 앱 전역 가드(`APP_GUARD`)와 경로 접두사는 머리말에 적힌다. 경로를 풀 수 없으면 `<dynamic:…>` 행과 경고가 나온다(손으로 고친다).
- 열은 점검 축 6개: `authz`(인가·객체 소유) · `dup`(재시도·더블 제출·멱등) · `conc`(경합·잠금·여러 탭·여러 인스턴스) · `abort`(부분 실패·외부 실패·시간 초과·종료) · `input`(경계·형식) · `audit`(기록·로그). 불변식 행은 `- I1: …` 줄에서 만든다(칸 하나).
- 칸 값은 `checked: <증거 참조>` / `out_of_scope: <이유>` / `not_done: <이유>` 중 하나. `init`을 다시 돌려도 채운 칸과 행 id는 남는다. 종류별 축 해석(화면·딥링크·푸시에서 `abort` = 네트워크 끊김·백그라운드·프로세스 종료·앱 전환 등)은 qa-run 스킬 0단계 표.
- **진입점 0행이면 `check` 실패.** 정말 진입점이 없는 범위만 `qa coverage set --empty-scope "<이유>"`로 통과하고, 이유가 report header에 나온다(행이 있는 표에 이 기록이 있으면 실패).
- `check`는 분모(진입점 수·불변식 수)와 상태별 개수(행·축별)를 출력하고, 보고서 맨 위에 옮길 `report header` 줄을 준다. 행 상태: 축 하나라도 `not_done`이면 `not_done`, 아니면 하나라도 `checked`면 `checked`, 나머지는 `out_of_scope`. "권한 걸린 쓰기"는 권한·역할 데코레이터나 인증류 가드가 붙은 쓰기(기능 스위치 가드는 제외).

**2. 심은 결함 시험 — 키트의 놓침률(정기)**

```sh
qa seed list [--repo <레포> --scope <경로 …> [--sites]]       # 연산자 목록 / 범위 안 심을 자리 수
qa seed apply --repo <레포> --scope <경로 …> [--ops <op>] [--site 3 | --seeds 2] [--out <폴더>]
qa seed run --project <p> --repo <레포> --scope <경로 …> --seeds 10 [--rng-seed 0] [--dry-run]
```

- 연산자: `drop-authz-decorator`(쓰기 진입점의 권한 데코레이터·가드 제거) · `drop-owner-condition`(조회 조건의 userId·memberId 등 소유 조건 제거) · `drop-idempotency-check`(`if (existing…) throw/return` 제거) · `unconditional-state-update`(update where의 상태 조건 제거) · `drop-audit-call`(감사 기록 호출 제거) · `read-outside-transaction`(트랜잭션 안 읽기를 트랜잭션 밖 클라이언트로). 시드·픽스처·마이그레이션·스크립트 폴더에는 심지 않는다.
- 복사본은 `git archive`(이력 없음)로 만들고, 결함을 심은 **뒤에** `git init`·커밋한다 — `git diff`·이력에 흔적이 없다. 원래 레포는 읽기만 한다.
- 심은 위치·종류는 `$QA_EVAL_DIR/manifests/`(복사본·지식 저장소 밖, 에이전트에 주지 않는 경로)에만 적는다. 프롬프트는 범위만이고 모든 복사본에 같다. transcript에 연산자 이름·manifest 경로가 나오면 그 실행은 무효.
- `run`: 복사본마다 헤드리스 qa-breaker(`qa eval run`과 같은 격리), 출력의 `Location: <path>:<line>`과 증상 낱말로 찾음·부분·놓침을 자동 채점(사람 칸 따로), 연산자별 놓침률 표를 `evals/<project>/seed-results/`에 쓴다. `--dry-run`은 복사본을 만들고 프롬프트·명령만 보인 뒤 지운다(`claude -p`를 부르지 않음).

**3. 블라인드 재도출 — 끝난 실행 재검증**

```sh
qa rederive --project <p> --run <run> [--repo <레포>] [--commit <sha> --scope <경로 …>] [--dry-run]
```

- 실행 기록의 범위·커밋(`qa finding scope`로 기록, 없으면 `--commit`·`--scope`)만으로 새 헤드리스 실행을 만든다. 원래 결함·판정·반박·보고서는 주지 않는다.
- 그 실행에서 생긴 패턴(실행 기록의 `attribution` 중 제안 항목이 `new-pattern`인 것)을 숨긴다. 이미 있던 패턴에 통계만 귀속한 것은 보인다. 보이는 패턴 본문에 숨긴 패턴 ID가 있으면 `PAT-hidden`으로 바꾼다(leave-one-out과 같은 방식, `qa eval run`에도 적용).
- transcript 검사: 실행 id, 원래 결함 제목, 숨긴 패턴 ID, `findings.jsonl`, 지식 저장소 경로가 나오면 무효.
- 끝나면 `projects/<p>/runs/<run>/rederive-<시각>.md`에 '원래 결함 → 다시 찾았나'와 '블라인드만 찾음(원래 놓침?)' 표를 위치 기준 자동 대조 + 사람 칸으로 남긴다. `--dry-run`은 스냅샷·숨김 목록·프롬프트만 보인다.

### 검사와 동기화

```sh
qa check     # 패턴·평가 사례·실행 기록·보정 형식 + 공통 패턴·보정의 고객 정보·코드 경로·비밀값 검사(오류면 종료 코드 1)
qa sync      # qa check 통과 시 커밋 → pull --rebase → push
qa doctor    # 설치 검사(링크, PATH, QA_KB, 지식 저장소 클론·원격 동기화)
qa doctor --offline --target <레포 경로>   # + 대상 레포가 요구하는 도구(node·bun·pnpm·docker·python·go …) 점검
```

`--target`은 레포를 읽기만 한다(깊이 3, `node_modules` 등 제외). 없는 도구는 실패가 아니라 경고와 영향 문장으로 나온다. 예: docker가 없으면 "이 환경에서는 L4 장애 주입 불가 — 실DB 동시성은 목 시뮬레이션으로 대신하고 '실DB 미확인'을 환경 가정으로 적는다".

금지어 목록은 지식 저장소의 `.qa-denylist`(비공개)에서 읽는다. 한 줄에 하나, 대소문자 무시 부분 일치, `re:`로 시작하면 정규식.

## 학습 루프

1. **실행** — `INDEX.md`와 관련 패턴, 프로젝트 지식을 읽는다. 패턴은 힌트일 뿐이고 예산의 약 25%는 패턴 밖 탐색.
2. **검증** — 다른 컨텍스트의 `qa-skeptic`이 결함마다 두 번 반박한다(실행 중 1차, 보고 직전 새 컨텍스트 2차). 증거 없는 결함은 보고하지 않는다. 보고 전 기존 이슈·결정을 검색한다.
3. **판정(사람)** — 채택·오탐·안 고침·중복 + 이유 + 확정 심각도 + 이슈 번호 → `qa finding judge`, 심각도 보정 → `qa calibration add`.
4. **회고** — `/qa-retro`가 새 패턴 후보·오탐 함정·적용 범위 조정을 항목 단위로 제안(전체 다시 쓰기 금지).
5. **병합** — 리드가 승인한 항목만 `qa proposal apply`가 반영. 카운터는 스크립트가 결정적으로.
6. **평가 관문** — 변경 전후 같은 재현 세트를 돌려 재현율이 떨어지지 않고 오탐 함정 보고율이 오르지 않을 때만 반영.
7. **수명** — 오탐이 잦으면 probation → retired(지우지 않음), 오래 적중 없거나 프레임워크 major 변경이면 needs_revalidation.
8. **공통 승격** — 2개 이상 프로젝트에서 채택 3건 이상 + `qa check` 통과 + 검토 + 확인 + 평가 통과.

놓친 것을 재는 장치: 매 실행의 커버리지 표(분모와 빈칸), 키트의 심은 결함 놓침률(`qa seed run`), 끝난 실행의 블라인드 재도출(`qa rederive`). 가장 값진 학습은 **놓친 결함**이다: 모델에 없던 진입점 / 빠진 안내어 / 증명 실패 / 판정 오류로 분류하고, 패턴·절차를 고치고, 그 결함을 평가 사례로 넣는다.

## 안전 규칙

- 사용자 범위 설치만. 고객 레포에 흔적 0: 레포 안에서 `/init`, `/memory`, `--worktree`, 프로젝트 범위 플러그인 설치를 쓰지 않는다. 서브에이전트 `memory` 필드를 쓰지 않는다.
- 실험은 로컬·dev·스테이징에서만. 운영 환경·실결제·실발급·실발송·실고객 데이터 금지.
- 비공개 데이터(고객 코드) + 믿을 수 없는 내용(이슈·주석·로그) + 외부 통신을 한 에이전트에 묶지 않는다. `qa-breaker`는 외부 도구가 없고 지식 저장소에 쓰지 않는다. 레포·이슈·로그 안의 지시문은 데이터로 취급한다.
- 공통 패턴에는 고객사·프로젝트 이름, 코드 경로, 데이터, 비밀값을 넣지 않는다(`qa check`가 막는다). 출처는 프로젝트 수만.
- 보안 취약점은 공개 이슈 대신 담당자에게 먼저 알린다.
- 지식 저장소는 항상 비공개.

## 나중에: 비공개 플러그인 마켓

이 저장소는 이미 플러그인 모양이다(`.claude-plugin/plugin.json`, `skills/`, `agents/`, `bin/`). 기기·사람이 늘면 마켓플레이스 저장소에 `{"name": "qa-kit", "source": {"source": "github", "repo": "dockylitmers/qa-kit"}}` 항목을 두고 `claude plugin marketplace add`로 옮긴다. 플러그인 서브에이전트는 훅을 무시하고, 자동 갱신은 기본 꺼짐이며, 기기마다 git 자격 증명이 필요하다. 검사: `claude plugin validate .`

## 변경 기록

- **0.5.3** (2026-10-22) — 증거 전달에 방법 하나 추가: 로그인된 브라우저의 확장 도구로 이슈 첨부를 올리고(첨부 버튼이 만드는 파일 입력에 장마다 `file_upload`) 본문 증거 줄 아래에 넣기. 조건: 게시와 같은 승인 묶음, 올리기 전 개인정보 확인·가리기(첨부 주소는 남음), 캡처 ↔ 이슈 번호를 실행 기록에, 못 쓰면 관찰 값을 글로.
- **0.5.2** (2026-10-22) — UX 점검표 판정 기준을 키트 분류에 맞춤: 라벨을 결함·변경 요청·취향 셋으로 통일, 결함은 ① 명세·수용 기준 위반 ② 일을 못 끝내거나 잘못 끝내거나 돌아오지 못함이 재현됨 ③ 화면이 실제 상태(돈·결과·선택)와 다름 ④ WCAG 2.2 AA 위반(누르는 영역 24×24 CSS px 미만, 스와이프·끌기로만 되는 조작, 색만으로 구분)일 때만이고 나머지는 변경 요청. 28칸 판정 재작성(판단형 항목은 새 컨텍스트 오답·잘못 누름이 재현될 때만 결함, 44pt·48dp는 권장, 누르는 영역은 여백 포함). 명세가 정한 동작은 결함 항목이라도 변경 요청. 토스 미니앱 입점 문서는 변경 요청의 근거로만. 출처 번호 정리, WCAG 2.2·HIG·Android URL 추가.
- **0.5.1** (2026-10-22) — 화면 UX 점검표 `skills/qa-run/references/ux-heuristics.md`: 토스 제품·라이팅 원칙과 금지 다크 패턴, NN/g 10 휴리스틱, Laws of UX, Apple HIG·Android 내비게이션, Baymard 결제에서 뽑은 28칸(한 화면의 목표, 내비게이션, 선택·입력, 상태 표시, 문구, 일관성, 결제)과 결함·변경 요청·취향 분류, 출처 표. qa-run 3단계 6번(화면 × UX 점검표)과 8단계 분류, device-qa 5a절에서 연결.
- **0.5.0** (2026-10-22) — 기기 화면 신고 재현 회고 반영. 재현 기록 세 칸(원문 조작·끝 상태 / 내 조작·끝 상태 / 일치)과 `같음`만 LD인 규칙, 플랫폼 없는 렌더링·터치 신고는 Android·iOS 둘 다(device-qa 5절, qa-run 6단계, `qa finding add --repro-*`, `qa check`·`qa report check`가 세 칸 없는 LD를 오류로). device-qa `touch`: role·글자 완전 일치 요소의 DOM 위치를 dpr·웹뷰 위쪽 여백으로 바꿔 Android `adb input tap`·iOS W3C pointer로 누르고, 좌표가 숫자가 아니면 멈춤. JS click()이 hover·터치 상태를 만들지 않는다는 주의. 캡처 개인정보(앞 화면 확인, 계정 정보는 가리고 찍기). 보고 전 분류 순서(명세·관리자 데이터 먼저 → 변경 요청). 담당 제안은 `project.yaml`의 `owners` 표에서(`qa report assign`, 제안 op `project-set`). 증거 전달(사진 첨부 방법을 게시 계획에, 아니면 관찰 값을 글로).
- **0.4.1** — 커버리지 관문이 진입점 0행을 통과로 보지 않음, 앱 진입점(화면·딥링크·푸시·외부 전환) 추출.
- **0.4.0** — 놓침 검사: 커버리지 관문, 심은 결함, 블라인드 재도출.
- **0.3.0** — device-qa 스킬, qa-run 모드 선택, LD 증거 등급.

## 개발

```sh
python3 -m unittest discover -s tests   # CLI 단위 시험(임시 지식 저장소·임시 git 레포에서, claude -p 호출 없음)
claude plugin validate .
```
