# 세션·잠금 원리

```text
없음
  └─ up → 잠금 획득 → 부하 확인 → 기기 선택/부팅 → Appium → 세션
세션
  ├─ open / eval / tap / type / shot / record
  ├─ native inspect → 네이티브 버튼 라벨 읽기
  ├─ permission RESOURCE ACTION → 명시된 버튼만 누르기
  └─ down → 녹화 중지 → 세션 삭제 → 내 Appium 종료 → 내가 켠 기기 종료 → 잠금 해제
```

1. 잠금은 `O_EXCL`로 만들고(`~/.agent-ops/device-locks/<device>.json`, `AGENT_OPS_DEVICE_LOCKS`로 바꿈) 다른 owner의 잠금을 빼앗지 않는다.
2. `up` 전에 잠금·프로세스·부하(1분 부하 ≤ 코어 수, 스왑 ≤ 12GB)를 확인하고, iOS와 Android가 동시에 켜지지 않게 한다. 잠근 뒤 다른 플랫폼 잠금을 한 번 더 본다.
3. `down`은 이 도구가 만든 프로세스와 기기만 정리한다. 이미 켜져 있던 남의 에뮬레이터는 쓰기만 하고 끄거나 지우지 않는다(`--wipe`·다른 `--avd`·앱 설치를 거부).
4. 종료 중 오류가 나면 잠금을 남겨 다른 세션이 같은 기기를 쓰지 못하게 한다.
5. `dismiss`는 거부·닫기 전용이고 허용은 `permission … allow`에서만 된다. `native inspect`는 읽기 전용이다.
6. `tap`은 iOS에서 실제 터치(WebDriverAgent), Android에서 W3C touch pointer를 쓰고, 화면이 멈출 때까지 기다린 뒤 누르며, 페이지가 본 첫 터치로 맞음·빗나감을 판정한다. 확대된 페이지·가려진 요소·화면 밖 요소는 누르지 않고 오류를 낸다.
7. 앱 모드의 `open`·`eval`·`tap`·`type`은 WEBVIEW 문맥에서만 실행한다.
8. iOS QA 시뮬레이터(`QA-agentops-<기기>`)는 세션 사이 켜 둔 채 남긴다(빠른 재시작). 비우기는 `ios reset`만.
