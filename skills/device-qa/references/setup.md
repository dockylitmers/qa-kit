# 기기 도구 설치 (최초 한 번)

`doctor`에서 이미 통과한 항목은 다시 설치하지 않는다. 사용자 시뮬레이터·AVD·파일은 지우거나 초기화하지 않는다. 실패한 명령은 숨기지 말고 명령·exit code·stderr·다음 조치를 적는다.

```sh
DQ=~/.claude/skills/device-qa/scripts/device_qa.py
python3 -I "$DQ" doctor --platform all
```

## iOS (사람이 할 것)

1. App Store에서 Xcode를 설치하고 한 번 실행해 라이선스·추가 구성요소를 끝낸다.
2. 별도 터미널에서(암호 필요):
   ```sh
   sudo xcode-select -s /Applications/Xcode.app
   sudo xcodebuild -runFirstLaunch
   ```
3. iOS 시뮬레이터 런타임이 없으면 Xcode › Settings › Components 또는 `xcodebuild -downloadPlatform iOS`(수 GB).

## 공통: Node·Appium

Node 20.19+ / 22.12+ / 24+, npm 10+.

```sh
npm install --global appium
appium driver install xcuitest        # iOS
appium driver install uiautomator2    # Android
```

`appium`이 PATH에 없으면 `npm prefix --global`의 `bin`을 PATH에 넣거나 `APPIUM_BIN`으로 지정한다. `setup --apply`가 이 단계를 대신할 수 있다.

## Android

Java 17+, Android SDK(emulator·platform-tools·android-35 arm64 이미지), QA 전용 AVD.

```sh
python3 -I "$DQ" setup --platform android          # 계획
python3 -I "$DQ" setup --platform android --apply  # openjdk@21·android-commandlinetools·SDK 패키지·QA AVD
```

- Homebrew가 Command Line Tools가 오래됐다며 설치를 거부하면 사람에게 소프트웨어 업데이트로 갱신을 요청한다. Node 패키지(예: bun)는 `npm install -g`로 우회할 수 있다.
- Intel Mac은 Homebrew 경로가 `/usr/local`일 수 있다. `doctor` 출력의 실제 경로를 기준으로 `JAVA_HOME`·`ANDROID_HOME`을 맞춘다. 에이전트 셸은 명령마다 환경 변수가 사라지므로 같은 호출 안에서 export한다.
- `setup --apply`는 `QA-agentops-` 접두사 AVD만 만든다.

## 확인

```sh
python3 -I "$DQ" doctor --platform all    # ok: true, 필수 항목 모두 통과
python3 -I "$DQ" status                   # []
```

설치 확인 단계에서는 `up`으로 기기를 켜거나 세션을 열지 않는다.
