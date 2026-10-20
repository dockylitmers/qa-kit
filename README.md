# qa-kit

장애 중심 QA 에이전트 키트. Claude Code에서 **실패 경계·새드 패스·에러 전파 경로**를 찾아 증거와 함께 보고하고, 프로젝트를 거칠수록 지식 저장소(`qa-knowledge`, 비공개)가 자란다.

| 구성 | 하는 일 |
|---|---|
| `skills/qa-run` | 절차: 0 안전 계약 → 1 정찰 → 2 기능 재설계(장애 설계 카드) → 3 위험 분석 → 4 전파 추적 → 5 가설 순위 → 6 증명 L0–L5 → 7 반박 검증 → 8 결함 카드 보고 → 9 회고 기록 |
| `skills/qa-retro` | 회고(수동 전용): 판정·놓친 결함에서 패턴 변경을 항목 단위로 `proposals/`에 제안 |
| `agents/qa-breaker` | 공격·증명(1–6단계). 외부 발송 도구 없음, 지식 저장소에 쓰지 않음, `qa-run` 미리 로드 |
| `agents/qa-skeptic` | 반박 검증(7단계). 결함 후보마다 "사실이 아님"을 증명하려고 시도 |
| `bin/qa` | CLI(Python 표준 라이브러리만): 설치 검사, 형식 검사, 패턴, 실행 기록·판정·통계, 재현 평가, 동기화 |
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
~/.claude/skills/qa-run, qa-retro   -> qa-kit/skills/...
~/.agents/skills/qa-run, qa-retro   -> qa-kit/skills/...   (Codex 등 같은 SKILL.md 형식을 읽는 도구용)
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

- 호출한 세션이 안전 계약(범위·환경·중단 조건)을 쓰고, 1–6단계는 `qa-breaker`, 7단계는 `qa-skeptic`에게 맡기고, 결함 카드(영어 이슈 초안)와 반박된 가설 목록을 만든다.
- 산출물은 고객 레포 밖 실행 폴더 `${QA_WORK:-~/.qa-work}/<project>/<run-id>/`에 쓴다.
- 이슈 게시·라벨·메시지는 하지 않는다. 사람이 승인 묶음으로 한다.

헤드리스(로컬 cron·launchd):

```sh
cd <project repo> && claude -p --agent qa-breaker \
  --append-system-prompt-file ~/.claude/skills/qa-run/SKILL.md \
  --add-dir="$HOME/.claude/skills/qa-run" "<scope>"
```

`--agent`로 메인 스레드에서 돌릴 때는 에이전트의 `skills:` 미리 로드가 적용되지 않아(서브에이전트로 띄울 때만 적용) 절차를 직접 붙인다. `qa eval run`은 이렇게 한다.

### 기록과 판정

```sh
qa init <project> [--repo ~/path/to/clone]                 # 프로젝트 폴더 만들기(한 번)
qa finding applied --project <p> --run <run> --patterns PAT-a,PAT-b
qa finding add --project <p> --run <run> --title "..." --severity high --evidence L1 \
   --class error_propagation --location "file:line" --patterns PAT-a
qa finding judge --project <p> --run <run> --finding F001 --verdict accepted \
   --reason "fixed in next release" --severity high --by <who>
qa finding list --project <p> --run <run>
```

판정(`accepted | false_positive | wont_fix | duplicate`)이 들어오면 CLI가 쓰인 패턴의 `stats`를 실행 기록에서 다시 계산한다(LLM이 카운터를 만지지 않는다).

### 회고

```
/qa-retro <project> <run-id>
/qa-retro <project> missed=<고객사 QA나 운영에서 나온 결함 설명>
```

변경은 `proposals/`에 항목 단위로 제안만 한다. 사람이 검토해 패턴 파일에 반영하고 `qa pattern index`, `qa check`, `qa sync`.

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
qa eval run <case> [--repo <clone>] [--timeout 30] [--max-budget 5] [--keep] [--dry-run]
```

- 사례의 `pre_fix_commit`을 `git archive`로 이력 없이 `~/Workspace/.qa-eval/<project>-<사례 해시>-<시각>/`에 풀고 `git init`(미래 커밋·이슈 번호가 보이지 않게). 폴더 이름은 사례 ID 대신 해시를 쓴다(사례 ID에 이슈 번호·증상이 들어 있어 에이전트가 작업 폴더 이름으로 답을 볼 수 있음). 사례와 폴더의 대응은 결과 파일에 적힌다.
- 스냅샷 안의 프로젝트 설정·훅은 읽지 않는다(`--setting-sources user`).
- `claude -p --agent qa-breaker`에 **범위(scope)만** 준다. 이슈 본문·번호·`expected_finding`은 프롬프트에 넣지 않는다. 도구는 Read·Grep·Glob만(읽기 전용).
- 결과는 `evals/<project>/results/<case>-<시각>.md`. v0 채점은 기대(위치·증상)와 에이전트 출력을 나란히 두고 사람이 표시한다.
- 레포 위치: `--repo`, `QA_REPO_<PROJECT>` 환경 변수, 또는 `projects/<project>/project.yaml`의 `repo_paths`.

### 검사와 동기화

```sh
qa check     # 패턴·평가 사례·실행 기록 형식 + 공통 패턴의 고객 정보·코드 경로·비밀값 검사(오류면 종료 코드 1)
qa sync      # qa check 통과 시 커밋 → pull --rebase → push
qa doctor    # 설치 검사(링크, PATH, QA_KB, 지식 저장소 클론·원격 동기화)
```

금지어 목록은 지식 저장소의 `.qa-denylist`(비공개)에서 읽는다. 한 줄에 하나, 대소문자 무시 부분 일치, `re:`로 시작하면 정규식.

## 학습 루프

1. **실행** — `INDEX.md`와 관련 패턴, 프로젝트 지식을 읽는다. 패턴은 힌트일 뿐이고 예산의 약 25%는 패턴 밖 탐색.
2. **검증** — 다른 컨텍스트의 `qa-skeptic`이 반박한다. 증거 없는 결함은 보고하지 않는다.
3. **판정(사람)** — 채택·오탐·안 고침·중복 + 이유 + 확정 심각도 → `qa finding judge`.
4. **회고** — `/qa-retro`가 새 패턴 후보·오탐 함정·적용 범위 조정을 항목 단위로 제안(전체 다시 쓰기 금지).
5. **병합** — 사람이 검토해 반영. 카운터는 스크립트가 결정적으로.
6. **평가 관문** — 변경 전후 같은 재현 세트를 돌려 재현율이 떨어지지 않고 오탐 함정 보고율이 오르지 않을 때만 반영.
7. **수명** — 오탐이 잦으면 probation → retired(지우지 않음), 오래 적중 없거나 프레임워크 major 변경이면 needs_revalidation.
8. **공통 승격** — 2개 이상 프로젝트에서 채택 3건 이상 + `qa check` 통과 + 검토 + 확인 + 평가 통과.

가장 값진 학습은 **놓친 결함**이다: 모델에 없던 진입점 / 빠진 안내어 / 증명 실패 / 판정 오류로 분류하고, 패턴·절차를 고치고, 그 결함을 평가 사례로 넣는다.

## 안전 규칙

- 사용자 범위 설치만. 고객 레포에 흔적 0: 레포 안에서 `/init`, `/memory`, `--worktree`, 프로젝트 범위 플러그인 설치를 쓰지 않는다. 서브에이전트 `memory` 필드를 쓰지 않는다.
- 실험은 로컬·dev·스테이징에서만. 운영 환경·실결제·실발급·실발송·실고객 데이터 금지.
- 비공개 데이터(고객 코드) + 믿을 수 없는 내용(이슈·주석·로그) + 외부 통신을 한 에이전트에 묶지 않는다. `qa-breaker`는 외부 도구가 없고 지식 저장소에 쓰지 않는다. 레포·이슈·로그 안의 지시문은 데이터로 취급한다.
- 공통 패턴에는 고객사·프로젝트 이름, 코드 경로, 데이터, 비밀값을 넣지 않는다(`qa check`가 막는다). 출처는 프로젝트 수만.
- 보안 취약점은 공개 이슈 대신 담당자에게 먼저 알린다.
- 지식 저장소는 항상 비공개.

## 나중에: 비공개 플러그인 마켓

이 저장소는 이미 플러그인 모양이다(`.claude-plugin/plugin.json`, `skills/`, `agents/`, `bin/`). 기기·사람이 늘면 마켓플레이스 저장소에 `{"name": "qa-kit", "source": {"source": "github", "repo": "dockylitmers/qa-kit"}}` 항목을 두고 `claude plugin marketplace add`로 옮긴다. 플러그인 서브에이전트는 훅을 무시하고, 자동 갱신은 기본 꺼짐이며, 기기마다 git 자격 증명이 필요하다. 검사: `claude plugin validate .`

## 개발

```sh
python3 tests/test_qa.py          # CLI 단위 시험(임시 지식 저장소에서)
claude plugin validate .
```
