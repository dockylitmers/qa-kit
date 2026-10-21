---
name: device-qa
description: Device proof tool for qa-run — drives an iOS simulator or Android emulator (mobile web or app/WebView mode) with per-device locks, real taps checked on the page, screenshots, recordings and explicit permission-prompt handling. Use from qa-run step 6 when the run mode is device or both, to prove device-boundary hypotheses (app switch and return, network loss, back button, background or process death, permissions, deep links and push entry). A plain "device QA" request starts qa-run, which asks the mode first. Also use to install or check the device toolchain.
argument-hint: "doctor | setup | <ios|android> <command> --owner <owner> ..."
---

# device-qa — 기기 재현 도구

qa-kit에서 **기기에서 증명하는 손**이다. 무엇을 무너뜨릴지는 `qa-run`이 정하고(0~5단계), 이 스킬은 그 가설을 실제 화면에서 재현해 증거로 남긴다(6단계 `LD`). 화면이 잘 나오는지 훑는 용도로 혼자 쓰지 않는다. "기기 QA 해줘" 같은 요청은 `/qa-run`으로 시작해 모드를 고르게 한다.

CLI: `DQ=~/.claude/skills/device-qa/scripts/device_qa.py` — 모든 명령은 `python3 -I "$DQ" …`. 결과는 JSON 한 줄, 실패는 stderr 한 줄과 exit 1.

## 1. 설치·점검

```sh
python3 -I "$DQ" doctor --platform all      # 읽기 전용, 필수 항목 실패면 exit 1
python3 -I "$DQ" setup --platform android   # 고칠 명령 계획(dry-run)
python3 -I "$DQ" setup --platform android --apply   # 자동 가능한 것만 설치, QA-agentops- 기기만 만든다
```

- Xcode 자체, iOS 런타임, Node는 사람이 설치한다. `setup --apply`는 Appium·드라이버·Java·Android SDK·QA 전용 AVD만 다룬다. 절차와 수동 조치: [references/setup.md](references/setup.md).
- `sudo`가 필요한 명령(`xcode-select -s`, `xcodebuild -runFirstLaunch`)은 사람에게 별도 터미널에서 실행해 달라고 한다. 에이전트 셸에서는 암호를 입력할 수 없다.

## 2. 안전 규칙 (반드시)

- **잠금**: 모든 명령에 고유 `--owner`(영문·숫자·`._-` 40자 이하, 예: `<run-id>-android`). 시작 전 `status --load`, 다른 owner의 잠금이 있으면 끄거나 뺏지 않고 기다리거나 묻는다.
- **iOS와 Android를 동시에 켜지 않는다**(도구가 막는다). 사람이 직접 켠 시뮬레이터·AVD·Appium을 끄지 않는다.
- **끝**: 같은 owner로 `down` → `status`가 `[]`. `[]`가 아니면 끝난 것이 아니다. 원인과 재시도를 기록한다.
- **권한 팝업**: 먼저 `native inspect`(읽기 전용). `permission <resource> allow`는 범위 파일(scope.md)에 그 권한의 허용 시험이 적혀 있을 때만. 버튼 이름만으로 어떤 권한인지 모르면 캡처의 제목·본문으로 확인하고, 근거가 없으면 누르지 않는다. 기본 `dismiss`는 거부·닫기만 한다.
- **앱 모드**: `context`가 WEBVIEW가 아니면 `open`·`eval`·`tap`·`type`을 쓰지 않는다. 앱이 WebView 디버깅을 켜지 않았으면 WEBVIEW가 안 보인다 — 그때는 캡처·네이티브 조작까지만 하고, 필요하면 디버깅만 켠 QA용 빌드를 사람에게 요청한다.
- **증거는 실행 폴더에**: `<run>/evidence/<가설 번호>/device/` 아래 절대 경로. 대상 레포 안(`docs/`, `local_docs/` 등)이나 세션 임시 폴더에 두지 않는다.
- 운영 환경에서 로그인·결제·예약·삭제를 하지 않는다. dev·스테이징에서도 쓰기 동작은 scope.md가 허용한 것만, 이 실행이 만든 항목에만 한다.
- `tap`이 `hit: false`나 exit 1이면 재시도하지 않고 그 결과와 화면을 증거로 남긴다(빗나간 터치를 반복하면 다른 것이 눌린다).

## 3. 명령

| 명령 | 하는 일 |
|---|---|
| `status [--load]` | 잠금 목록(+ 부하 측정값) |
| `<p> up --owner O --url URL` | 기기 켜기 + 브라우저(iOS Safari / Android Chrome) |
| `ios up --owner O --app /abs/App.app` · `--bundle-id ID` | 앱 모드(시뮬레이터 빌드 `.app`) |
| `android up --owner O --apk /abs/app.apk` · `--package P --activity A` | 앱 모드. 권한 UX를 볼 때 `--no-auto-grant-permissions` |
| `<p> context [NATIVE_APP|WEBVIEW|이름]` | 앱 모드 문맥 보기·바꾸기 |
| `<p> open URL` · `eval EXPR` · `eval --script FILE` | 이동, 페이지 값 읽기(식 하나. 문장 여럿은 `(()=>{…})()` 또는 `--script`) |
| `<p> tap SEL` · `tap SEL --js` | 실제 터치 + 페이지가 본 첫 터치로 판정(`hit`, `href`, `event`). `--js`는 터치가 시험 대상이 아닐 때만 |
| `<p> type TEXT --selector SEL [--clear]` | 입력(포커스 없으면 먼저 실제 터치) |
| `<p> shot PATH` · `record start --path P` / `record stop` | 캡처, 녹화(Android 최대 180초) |
| `<p> native inspect` · `dismiss` · `permission R A` | 네이티브 버튼 읽기, 닫기, 권한 처리(R: microphone camera location notifications photos bluetooth tracking / A: allow deny close inspect) |
| `ios push PAYLOAD.json` | 시뮬레이터 앱에 푸시(앱 모드) |
| `<p> down` · `ios reset` | 끄기·잠금 해제, QA 시뮬레이터 비우기 |

자세한 예와 실패 처리 표: [references/commands.md](references/commands.md). 잠금·종료 원리: [references/logic.md](references/logic.md).

## 4. 기기 경계 가설 (qa-run 3단계 대입표)

qa-run이 기기 모드일 때 흐름의 각 단계에 아래를 대입한다. 불변식은 qa-run 2단계 카드에서 가져온다(예: 돈은 한 번만 걷힌다, 결제 결과가 사라지지 않는다, 로그인 없이 보호 화면이 보이지 않는다).

| 경계 | 흔들기 | 무엇이 깨지나 |
|---|---|---|
| 앱 전환·복귀 | 결제·인증 앱으로 넘어갔다 돌아오기, 넘어간 사이 앱 프로세스 종료 | 결과 유실, 중복 요청, 미결 표시 |
| 네트워크 | 승인·복귀·저장 순간 끊김(비행기 모드), 느린 회선, 복구 뒤 "다시 시도" | 성공처럼 보이는 실패, 중복 제출, 멈춘 로딩 |
| 뒤로 가기 | 결제창·결과·오류 화면에서 시스템 뒤로 | 두 번 열림, 결제된 것이 미결로 보임, 앱 종료 |
| 생명주기 | 홈 갔다 오기, 강제 종료 뒤 재실행, WebView 재생성 | 로그인·장바구니·진행 상태 유실 |
| 연타·동시 | 제출 버튼 실제 터치 2회, 두 화면에서 같은 동작 | 주문·결제·발급 중복 |
| 진입점 | 딥링크·푸시로 보호 화면·없는 항목·잘못된 값 열기(로그인/비로그인) | 권한 밖 노출, 빈 화면, 오류 화면 |
| 권한 | 거부 → 기능 사용 → 다시 묻기 | 막힌 기능이 조용히 실패 |
| 외부 링크 | 웹 안 외부 주소·전화·메일·앱 스킴 | 앱 밖으로 새거나, 나가야 할 것이 막힘 |
| 화면·입력 | 작은 폭(360dp), 큰 글자, 키보드가 덮는 버튼, 회전 | 핵심 버튼이 가려지거나 눌리지 않음 |
| 시작 | 오프라인으로 시작, 첫 로드 실패 | 시작 화면이 멈춤, 복구 길 없음 |

## 5. 기기 재현 증거 (`LD`)

`evidence/<가설 번호>/device/README.md`에: 기기·OS·빌드(앱 버전·커밋, WebView 디버깅 여부), owner, 실행한 명령과 출력(`up`의 `timings`·`href`, `tap`의 `hit`·`event`), 단계별 캡처·녹화 파일 이름, 기대(명세 근거)·실제, 바꾼 기기 설정과 원상복구 확인, 재현 횟수(예: 2/2). 증거 등급은 `LD`(실제 화면에서 재현됨). 재현하지 못한 가설은 코드 근거(`L0`)로 남긴다.

## 6. 실기기

이 스크립트는 시뮬레이터·에뮬레이터만 다룬다(`adb devices`에서 `emulator-` 줄만 본다). 실제 폰은 같은 엔진(Appium)을 직접 쓴다:
- Android: USB 디버깅 허용 → `appium server --port <4791이 아닌 포트> --allow-insecure=uiautomator2:chromedriver_autodownload`, capabilities `platformName=Android, appium:automationName=UiAutomator2, appium:udid=<serial>, appium:appPackage, appium:appActivity, appium:noReset=true`. 디버그 키로 서명한 빌드는 Play 프로텍트 확인 창이 떠서 사람이 "무시하고 설치"를 눌러야 한다.
- iOS: 개발자 모드, Safari 웹 속성, Xcode 서명 팀(WebDriverAgent 설치·신뢰)이 필요하다.
- 도구가 잠금을 대신 걸지 않으므로 `~/.agent-ops/device-locks/<platform>-real-<serial>.json`에 owner를 적어 두고 끝나면 지운다. 바꾼 폰 설정(비행기 모드·글꼴 크기·회전)은 시험마다 되돌리고 끝에 처음 값과 비교한다. 남의 폰일 수 있으니 다른 앱·개인 알림이 보이는 캡처는 실행 폴더 밖으로 내보내지 않는다.

## 7. 알려진 한계

- Android만 쓸 때도 부하 측정이 `xcrun simctl`을 부른다. 개발자 경로가 Command Line Tools면 `android up`이 실패한다 → `xcode-select -s /Applications/Xcode.app` 또는 명령마다 `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`(처음 한 번 Xcode가 시뮬레이터 구성요소를 설치할 수 있다).
- 영어 기본 AVD에서 한국어 페이지를 열면 Chrome 번역 말풍선이 머리글을 가리고 `native inspect`·`dismiss`가 다루지 못한다(몇 초 뒤 사라짐). 캡처는 사라진 뒤에.
- `tap`은 누르기 전에 입력칸 포커스를 뺀다 → 포커스가 빠지면 닫히는 자동완성 목록은 실제 터치로 못 누른다(Appium의 stale element 오류). 이 오류를 앱 결함으로 올리지 않는다. 기능 확인만 `tap --js`.
- 회전·글자 크기·화면 폭을 바꾸는 명령이 없다. `adb shell settings put system user_rotation 1`은 세션이 유지되지만, `font_scale`·`wm density`는 Chrome 창을 다시 만들어 세션이 끊긴다(`down` → `up`).
- `down`은 성공해도 출력이 없다(exit 0). 이어서 `status`로 확인한다.
- `doctor`는 점 없는 버전 표기("openjdk 27")를 읽지 못해 Java를 실패로 본다 → `openjdk@21` 설치로 우회.
