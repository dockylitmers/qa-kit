# 명령 예와 실패 처리

```sh
DQ=~/.claude/skills/device-qa/scripts/device_qa.py
RUN=${QA_WORK:-$HOME/.qa-work}/<project>/<run-id>
EV="$RUN/evidence/H03/device"; mkdir -p "$EV"
O=<run-id>-android
```

## 브라우저(모바일 웹)

```sh
python3 -I "$DQ" status --load
python3 -I "$DQ" android up --owner "$O" --url https://<dev-host>/<path>
python3 -I "$DQ" android eval --owner "$O" 'location.href'
python3 -I "$DQ" android tap --owner "$O" '#submit'
python3 -I "$DQ" android type --owner "$O" '홍길동' --selector '#name' --clear
python3 -I "$DQ" android shot --owner "$O" "$EV/01-before.png"
python3 -I "$DQ" android down --owner "$O"
python3 -I "$DQ" status
```

iOS는 `android`를 `ios`로 바꾼다. iOS는 세션 사이 쿠키·탭이 남는다. 깨끗한 상태가 필요하면 `ios down` 뒤 `ios reset`.

## 실제 터치로 누르기(hover·터치 상태 시험)

```sh
python3 -I "$DQ" android touch --owner "$O" --role tab --text 'Recent'
# → {"hit": true, "via": "adb input tap", "point": [539, 318], "css": [196, 30], "dpr": 2.75, "webview": {...}}
python3 -I "$DQ" android eval --owner "$O" '(()=>{const e=[...document.querySelectorAll("[role=tab]")].find(x=>x.innerText.trim()==="Recent");return {color:getComputedStyle(e).color,hover:e.matches(":hover")}})()'
python3 -I "$DQ" ios touch --owner "$O" --role button --text '다음'
```

- 좌표 변환: Android 점 = 웹뷰 픽셀 원점(상태 표시줄·주소창 아래) + CSS 좌표 × `devicePixelRatio`. iOS 점 = 웹뷰 포인트 원점 + CSS 좌표 × (웹뷰 폭 포인트 / `innerWidth`). 웹뷰 위치는 NATIVE_APP 문맥의 WebView 요소 사각형에서 읽고 원래 문맥으로 돌아온다.
- 찾기는 role(`button`·`link`는 태그의 기본 role 포함)과 글자 또는 `aria-label`의 **완전 일치**(공백 접기)만. 0개나 2개 이상이면 누르지 않는다.
- 셸에서 명령과 인자를 변수 하나에 담지 않는다(zsh는 나누지 않는다). 여러 단계는 스크립트 파일로 만든다.

## 앱·WebView

```sh
python3 -I "$DQ" android up --owner "$O" --apk /abs/path/app.apk [--no-auto-grant-permissions]
python3 -I "$DQ" android context --owner "$O"            # 문맥 목록
python3 -I "$DQ" android context --owner "$O" WEBVIEW    # 첫 WebView로
python3 -I "$DQ" ios up --owner "$O" --app /abs/path/App.app
```

WebView가 30초 안에 보이지 않으면 `up` 결과에 `"webview": "없음 30초"`가 붙고 NATIVE_APP에 머문다. 앱이 WebView 디버깅을 켰는지 확인한다.

## 권한·네이티브 화면

```sh
python3 -I "$DQ" android native inspect --owner "$O"                  # 버튼 읽기만
python3 -I "$DQ" android permission camera deny --owner "$O"
python3 -I "$DQ" ios permission notifications allow --owner "$O"      # scope.md에 허용 시험이 있을 때만
```

## 녹화

```sh
python3 -I "$DQ" android record start --owner "$O" --path "$EV/flow.mp4"
python3 -I "$DQ" android record stop --owner "$O"
```

`down`은 녹화 중이면 먼저 멈춘다. 종료 전에 `find "$EV" -type f | sort`로 파일을 확인한다.

## 실패 처리

| 증상 | 조치 |
|---|---|
| `…는 <owner>가 쓰는 중입니다` | `status`로 주인 확인, 기다리거나 묻는다. 뺏지 않는다 |
| `부하 높음` | `status --load`, 내 기기만 끄고 다시 |
| `부하를 재지 못했습니다 … simctl` | Xcode 개발자 경로 설정(SKILL.md 7절) |
| `웹뷰가 아닙니다` | `context WEBVIEW` 뒤 다시 |
| `처리할 네이티브 버튼이 없습니다` | `native inspect`로 라벨 확인, 맞는 권한 명령 선택 |
| `빗나감: …` / `hit: false` | 재시도하지 않고 결과·캡처를 증거로 |
| `완전히 같은 요소가 없습니다` / `…개입니다` | 글자를 화면 그대로(공백·대소문자) 다시 확인하거나 role을 바꾼다. 부분 일치로 바꾸지 않는다 |
| `좌표가 숫자가 아닙니다` / `웹뷰 … 밖입니다` | 누르지 않았다. 페이지가 다 그려졌는지, 웹뷰가 하나인지 `native inspect`·캡처로 확인 |
| 이동이 `open`에서 멈춤(WebDriver navigate 불안정) | `eval 'location.href="<주소>"'`로 이동한 뒤 `eval 'location.href'`로 확인 |
| `stale element` (자동완성 등) | 키트 한계(SKILL.md 7절). 기능만 `tap --js` |
| `web view not found` | Chrome 창이 다시 만들어짐 → `down` 뒤 `up` |
| Android `offline` | 에뮬레이터가 다 켜지거나 꺼질 때까지 기다림 |
| `down`이 잠금을 남김 | 출력의 원인을 해결하고 같은 owner로 `down` 다시 |
