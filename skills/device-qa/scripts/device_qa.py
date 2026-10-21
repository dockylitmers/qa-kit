#!/usr/bin/env python3
"""Device QA helpers: one session per device, a reusable QA-only iOS simulator driven by
Appium XCUITest, Android emulator start/stop.

iOS: taps are native (WebDriverAgent, `nativeWebTap`) and page state is read through Appium's remote debugger.
Only the simulator named QA_PREFIX… is used, it is kept between sessions (erase only with `ios reset`), and only
processes this tool started are stopped.
"""
import argparse
import base64
import json
import os
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEVICES = ("ios", "android")
QA_PREFIX = "QA-agentops-"
OWNER = re.compile(r"[A-Za-z0-9._-]{1,40}")
SDK_CANDIDATES = ("~/Library/Android/sdk", "/opt/homebrew/share/android-commandlinetools")
APPIUM_PORT = 4791
# Safari's first launch on a fresh boot is slower than Appium's 5 s default; native taps make iOS-only
# behaviour (input zoom) happen. No retry loops — a failure is reported as is.
IOS_CAPS = {"appium:webviewConnectTimeout": 60000, "appium:nativeWebTap": True,
            "appium:connectHardwareKeyboard": False}  # the soft keyboard shows, as on a phone
WDA_MARKER = "APPIUM_XCODEBUILD_WDA_MARKER=1"
W3C_ELEMENT = "element-6066-11e4-a52e-4f735466cecf"
# Safari's own screens over the page (address field editing, "save password?"), most specific first
# an app's own permission prompt (notifications) is refused the same way; the label set is confirmed on a device
DISMISS_LABELS = ("허용 안 함", "허용 안함", "Don't Allow", "지금 안 함", "Not Now", "나중에", "Later", "취소", "Cancel", "닫기", "Close")
# Android Chrome through UiAutomator2 on the same Appium server; chromedriver is fetched to
# match the emulator's Chrome (server flag), app permission prompts are granted so Chrome itself does not stall.
ANDROID_CAPS = {"appium:autoGrantPermissions": True}
ANDROID_APPIUM_FLAG = "--allow-insecure=uiautomator2:chromedriver_autodownload"
# Chrome's own screens over the page (permission, "save password?", translate), most specific first
ANDROID_DISMISS_LABELS = ("허용 안함", "허용 안 함", "거부", "차단", "안 함", "저장 안함", "Never", "Deny", "Block", "No thanks", "취소", "Cancel", "닫기", "확인", "OK")
NATIVE_BUTTONS = {"ios": ("XCUIElementTypeButton", "label", DISMISS_LABELS, "XCUIElementTypeWebView"),
                  "android": ("android.widget.Button", "text", ANDROID_DISMISS_LABELS, "android.webkit.WebView")}
PERMISSION_LABELS = {
    "microphone": {"allow": ("허용", "Allow", "While Using the App", "앱 사용 중 허용", "한 번 허용", "Allow Once"),
                   "deny": DISMISS_LABELS + ANDROID_DISMISS_LABELS},
    "camera": {"allow": ("허용", "Allow", "While Using the App", "앱 사용 중 허용", "한 번 허용", "Allow Once"),
                "deny": DISMISS_LABELS + ANDROID_DISMISS_LABELS},
    "location": {"allow": ("앱을 사용하는 동안 허용", "앱 사용 중 허용", "정확한 위치 사용", "허용", "Allow While Using the App", "While Using the App", "Allow"),
                 "deny": DISMISS_LABELS + ANDROID_DISMISS_LABELS},
    "notifications": {"allow": ("허용", "Allow"), "deny": DISMISS_LABELS + ANDROID_DISMISS_LABELS},
    "photos": {"allow": ("모든 사진 허용", "전체 사진 허용", "사진 선택", "Allow Full Access", "Allow Access to All Photos", "Allow"),
               "deny": DISMISS_LABELS + ANDROID_DISMISS_LABELS},
    "bluetooth": {"allow": ("허용", "Allow"), "deny": DISMISS_LABELS + ANDROID_DISMISS_LABELS},
    "tracking": {"allow": ("허용", "Allow"), "deny": ("앱에 추적 금지 요청", "Ask App Not to Track", "Don't Allow")},
}
SETTLE_READS, SETTLE_TIMEOUT, HIT_TIMEOUT = 3, 5, 3
# Starting points to be adjusted by measurement —
# there is no override flag.
LIMITS = {"swap_gb": 12.0, "load1_per_core": 1.0}
# what `setup` installs and the QA AVD it creates; `android up` starts that AVD by default
QA_AVD_IMAGE = "system-images;android-35;google_apis;arm64-v8a"
QA_AVD = QA_PREFIX + "pixel7-35"
SDK_PACKAGES = ("platform-tools", "emulator", "platforms;android-35", QA_AVD_IMAGE)
BREW_SDK = "/opt/homebrew/share/android-commandlinetools"
JAVA_CANDIDATES = ("/opt/homebrew/opt/openjdk@21", "/opt/homebrew/opt/openjdk@17", "/opt/homebrew/opt/openjdk")
NODE_RANGES = (((20, 19, 0), (21, 0, 0)), ((22, 12, 0), (23, 0, 0)), ((24, 0, 0), None))  # Appium 3 engines
# app mode — how long `up` waits for a WEBVIEW_* context (no
# `appium:autoWebview`, which fails the whole session when no web view appears) and Android's screenrecord limit
WEBVIEW_WAIT_S = 30
RECORD_LIMIT_S = 180
APP_FLAGS = {"ios": ("--app", "--bundle-id", ".app"), "android": ("--apk", "--package", ".apk")}

class DeviceQAError(Exception):
    pass


def default_lock_dir():
    return Path(os.environ.get("AGENT_OPS_DEVICE_LOCKS", "~/.agent-ops/device-locks")).expanduser()


def run(cmd, check=True):
    try:
        proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeviceQAError(f"실행 실패: {' '.join(map(str, cmd))}: {error}")
    if check and proc.returncode != 0:
        raise DeviceQAError(f"실행 실패(exit {proc.returncode}): {' '.join(map(str, cmd))}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def spawn(cmd, log, env=None):
    with open(log, "ab") as out:
        return subprocess.Popen([str(c) for c in cmd], stdout=out, stderr=out, start_new_session=True, env=env).pid


def kill_pid(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def interrupt_pid(pid):
    """SIGINT: `simctl io recordVideo` finishes its file only on an interrupt."""
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        pass


def process_command(pid):
    proc = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return proc.stdout.strip()


def wait_android_boot(run, adb, serial, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        if run([adb, "-s", serial, "shell", "getprop", "sys.boot_completed"], check=False).strip() == "1":
            return True
        time.sleep(2)
    return False


# ---- locks -------------------------------------------------------------------------------------------------

def _lock_path(lock_dir, device, owner=None):
    if device not in DEVICES:
        raise DeviceQAError(f"기기는 {', '.join(DEVICES)} 중 하나입니다: {device}")
    if owner is not None and not OWNER.fullmatch(owner or ""):
        raise DeviceQAError(f"owner는 영문·숫자·._- 1~40자입니다(세션 이름): {owner!r}")
    return Path(lock_dir) / f"{device}.json"


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as error:  # empty while another session is writing it, or damaged
        raise DeviceQAError(f"잠금 파일 읽기 실패: {path} ({error}). 다른 세션이 쓰는 중이면 잠시 뒤 다시, "
                            f"깨졌으면 주인을 확인한 뒤 사람이 지웁니다")
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("device", "owner", "acquired_at")):
        raise DeviceQAError(f"잠금 파일 읽기 실패: {path} (형식이 다름). 주인을 확인한 뒤 사람이 지웁니다")
    return data


def _write(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _held_by_other(path, lock):
    return DeviceQAError(f"{lock['device']}은 {lock['owner']}가 쓰는 중입니다({lock['acquired_at']}~). 끄거나 뺏지 말고 "
                         f"그 세션에 묻습니다. 주인이 없는 게 확실하면 사람이 {path}를 지웁니다")


def acquire(lock_dir, device, owner):
    path = _lock_path(lock_dir, device, owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = {"device": device, "owner": owner, "acquired_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        current = _read(path)
        if current and current.get("owner") == owner:
            return current
        raise _held_by_other(path, current or {"device": device, "owner": "?", "acquired_at": "?"})
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(lock, out, ensure_ascii=False, indent=2)
    return lock


def own(lock_dir, device, owner):
    path = _lock_path(lock_dir, device, owner)
    lock = _read(path)
    if lock is None:
        raise DeviceQAError(f"{device} 잠금이 없습니다. 먼저 `{device} up --owner {owner}`")
    if lock.get("owner") != owner:
        raise _held_by_other(path, lock)
    return lock


def update(lock_dir, device, owner, **fields):
    lock = own(lock_dir, device, owner)
    lock.update(fields)
    _write(_lock_path(lock_dir, device), lock)
    return lock


def release(lock_dir, device, owner):
    own(lock_dir, device, owner)
    _lock_path(lock_dir, device).unlink()


def status(lock_dir):
    return [lock for d in DEVICES if (lock := _read(_lock_path(lock_dir, d)))]


# ---- load ------------------------------------------------------------------------

def current_load():
    return os.getloadavg()[0], os.cpu_count()


def measure_load(lock_dir, run=run):
    """Swap, compressed memory, 1-minute load, booted simulators, running emulators and device locks. A reading
    that cannot be taken is an error (the gate does not guess); compressed memory is only shown."""
    try:
        used = re.search(r"used = ([0-9.]+)M", run(["sysctl", "-n", "vm.swapusage"]))
        if not used:
            raise ValueError("vm.swapusage에 used 값이 없음")
        load1, cores = current_load()
        if not isinstance(cores, int) or cores < 1 or load1 != load1:
            raise ValueError(f"부하 {load1}, 코어 {cores}")
        groups = json.loads(run(["xcrun", "simctl", "list", "devices", "booted", "-j"]))["devices"]
        simulators = [{"name": d["name"], "udid": d["udid"]} for group in groups.values() for d in group
                      if d.get("state") == "Booted"]
        emulators = sum(len(parts) > 1 and Path(parts[1]).name.startswith("qemu-system")  # the program, not a grep
                        for parts in (line.split() for line in run(["ps", "-ax", "-o", "pid=,command="]).splitlines()))
    except (DeviceQAError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise DeviceQAError(f"부하를 재지 못했습니다(sysctl vm.swapusage·simctl·ps·getloadavg): {error}. up하지 않았습니다")
    compressed = None
    try:
        vm = run(["vm_stat"])
        page = int(re.search(r"page size of (\d+) bytes", vm).group(1))
        occupied = int(re.search(r"Pages occupied by compressor:\s+(\d+)", vm).group(1))
        compressed = round(occupied * page * 100 / int(run(["sysctl", "-n", "hw.memsize"]).strip()), 1)
    except (DeviceQAError, ValueError, AttributeError, ZeroDivisionError):
        pass  # shown only, no limit
    return {"swap_gb": round(float(used.group(1)) / 1024, 2), "compressed_pct": compressed, "load1": round(load1, 2),
            "cores": cores, "simulators": simulators, "emulators": emulators,
            "locks": [{"device": lock["device"], "owner": lock["owner"]} for lock in status(lock_dir)]}


def load_warning():
    """None when the load is fine or cannot be read — a tap's own result or error is never replaced."""
    try:
        load1, cores = current_load()
    except OSError:
        return None
    if not isinstance(cores, int) or cores < 1 or load1 != load1:
        return None
    limit = cores * LIMITS["load1_per_core"]
    if load1 > limit:
        return f"1분 부하 {load1:.1f} > {limit:g}(코어 {cores}) — 탭이 늦거나 빗나갈 수 있습니다"
    return None


def _gate(lock_dir, device, run=run):
    """Before a new `up`: refuse a busy Mac and the other platform being on (one iOS and one Android device are
    not on together). Returns the reading and, for Android, the unlocked QA simulators to stop."""
    reading = measure_load(lock_dir, run)
    limit = reading["cores"] * LIMITS["load1_per_core"]
    reasons, to_stop = [], []
    if reading["load1"] > limit:
        reasons.append(f"1분 부하 {reading['load1']:g} > {limit:g}(코어 {reading['cores']})")
    if reading["swap_gb"] > LIMITS["swap_gb"]:
        reasons.append(f"스왑 {reading['swap_gb']:g}GB > {LIMITS['swap_gb']:g}GB")
    other = "android" if device == "ios" else "ios"
    reasons += [f"{other} 잠금 {lock['owner']}(iOS·Android 동시 금지)" for lock in reading["locks"] if lock["device"] == other]
    if device == "ios":
        if reading["emulators"]:
            reasons.append(f"켜진 Android 에뮬레이터 {reading['emulators']}개")
        others = [sim for sim in reading["simulators"] if not sim["name"].startswith(QA_PREFIX)]
        if others:
            reasons.append("켜진 다른 시뮬레이터: " + ", ".join(sim["name"] for sim in others))
    else:
        mine = [sim for sim in reading["simulators"] if sim["name"].startswith(QA_PREFIX)]
        others = [sim for sim in reading["simulators"] if sim not in mine]
        if others:
            reasons.append("켜진 시뮬레이터(이 도구 것이 아님): " + ", ".join(sim["name"] for sim in others))
        to_stop = mine  # the tool's own warm QA simulator
    if reasons:
        raise DeviceQAError(f"부하 높음: {'; '.join(reasons)} — 기다리거나 내 기기 끄기(남의 기기는 끄지 않음). "
                            f"측정 {json.dumps(reading, ensure_ascii=False)}")
    return reading, to_stop


def _locked_after_gate(lock_dir, device, owner):
    """The other platform may have locked between the reading and our lock: look again now that we
    hold ours, and give ours back if so."""
    other = "android" if device == "ios" else "ios"
    held = _read(_lock_path(lock_dir, other))
    if held:
        release(lock_dir, device, owner)
        raise DeviceQAError(f"부하 높음: 잠근 뒤 다시 보니 {other} 잠금 {held['owner']}(iOS·Android 동시 금지) — "
                            f"기다리거나 내 기기 끄기. {device} 잠금은 풀었습니다")


def _already_up(lock_dir, device, owner, key):
    lock = _read(_lock_path(lock_dir, device, owner))
    return bool(lock and lock.get("owner") == owner and lock.get(key))


# ---- iOS (Appium XCUITest) ---------------------------------------------------------------------------------

def find_appium():
    candidates = [os.environ.get("APPIUM_BIN"), shutil.which("appium")]
    try:  # npm's global bin is often missing from PATH
        prefix = subprocess.run(["npm", "config", "get", "prefix"], capture_output=True, text=True, timeout=30).stdout.strip()
        candidates.append(str(Path(prefix) / "bin" / "appium") if prefix else None)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for candidate in filter(None, candidates):
        if Path(candidate).exists():
            return candidate
    raise DeviceQAError("appium을 찾지 못했습니다. `doctor --platform ios` 참고")


def webdriver(method, url, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, method=method, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response).get("value")
    except urllib.error.HTTPError as error:
        try:
            message = json.load(error).get("value", {}).get("message", "")
        except ValueError:
            message = str(error)
        raise DeviceQAError(f"Appium {method} /{url.split('/', 3)[-1]}: {message[:400]}")
    except (OSError, ValueError) as error:
        raise DeviceQAError(f"Appium 연결 실패 {url}: {error}")


def _server(port):
    return f"http://127.0.0.1:{port}"


def _port_in_use(port):
    """Any listener counts, not only a WebDriver one: a plain server would make Appium wait 60 s."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def _server_alive(port):
    try:
        webdriver("GET", f"{_server(port)}/status", timeout=3)
        return True
    except DeviceQAError:
        return False


def _qa_name(device_type):
    return QA_PREFIX + re.sub(r"\s+", "-", device_type.strip())


def _newest_ios_runtime(run):
    runtimes = [r for r in json.loads(run(["xcrun", "simctl", "list", "runtimes", "-j"]))["runtimes"]
                if r.get("isAvailable") and r.get("platform", "iOS") == "iOS"]
    if not runtimes:
        raise DeviceQAError("설치된 iOS 런타임이 없습니다(Xcode › Settings › Components)")
    return max(runtimes, key=lambda r: [int(x) for x in r["version"].split(".")])["identifier"]


def _devices(run):
    groups = json.loads(run(["xcrun", "simctl", "list", "devices", "-j"]))["devices"]
    return [device for group in groups.values() for device in group]


def ios_up(lock_dir, owner, url=None, run=run, device_type="iPhone 17", port=APPIUM_PORT, log=None, app=None, bundle_id=None):
    spec = _app_spec("ios", url, app, bundle_id)  # before any lock: a wrong combination changes nothing
    fresh = not _already_up(lock_dir, "ios", owner, "session_id")
    if fresh:
        _gate(lock_dir, "ios", run)
    acquire(lock_dir, "ios", owner)
    if fresh:
        _locked_after_gate(lock_dir, "ios", owner)
    lock = own(lock_dir, "ios", owner)
    if lock.get("session_id"):  # already up: an `up --url` still opens the URL, like `open`
        refusal = _state_change_refusal("ios", "session", True, lock["udid"], app=spec is not None)
        if refusal:
            raise DeviceQAError(refusal)
        return {**lock, **ios_open(lock_dir, owner, url)} if url else lock
    started = time.time()
    name = _qa_name(device_type)
    found = [d for d in _devices(run) if d["name"] == name]
    udid = found[0]["udid"] if found else run(["xcrun", "simctl", "create", name, device_type, _newest_ios_runtime(run)]).strip()
    update(lock_dir, "ios", owner, udid=udid, name=name)
    run(["xcrun", "simctl", "bootstatus", udid, "-b"])
    booted = time.time()
    spec = _installed_app(run, "ios", udid, spec)
    served = _appium_server(lock_dir, "ios", owner, port, log)
    lock = _open_session(lock_dir, "ios", owner, port, udid, spec)
    done = time.time()
    # step times and the Safari tabs left from earlier sessions — shown, not kept in the lock
    shown = {"timings": {"boot_s": round(booted - started, 1), "server_s": round(served - booted, 1),
                         "session_s": round(done - served, 1), "total_s": round(done - started, 1)},
             "safari_tabs": None if spec else _safari_tabs(lock)}
    if spec:
        shown.update(_enter_webview(lock))
    return {**lock, **shown, **ios_open(lock_dir, owner, url)} if url else {**lock, **shown}


# ---- app mode ------------------------------------------------------------------

def _app_spec(device, url, path=None, app_id=None, activity=None):
    """The app an `up` opens, or None for the browser. Every argument check happens here, before any lock."""
    file_flag, id_flag, suffix = APP_FLAGS[device]
    if device == "android" and (app_id is None) != (activity is None):
        raise DeviceQAError("--package와 --activity는 함께 줍니다(예 --package kr.x --activity .MainActivity)")
    if path is None and app_id is None:
        return None
    if url:
        raise DeviceQAError(f"앱 모드에는 --url을 쓰지 않습니다(앱이 자기 주소를 엽니다): {file_flag if path else id_flag}")
    if path is not None and app_id is not None:
        raise DeviceQAError(f"{file_flag}와 {id_flag} 가운데 하나만 줍니다")
    if app_id is not None:
        return {"id": app_id, "source": "bundle", **({"activity": activity} if device == "android" else {})}
    app = Path(path)
    if not app.is_absolute():
        raise DeviceQAError(f"{file_flag}는 절대 경로입니다(Appium 서버가 읽음): {path}")
    if not app.exists():
        raise DeviceQAError(f"{file_flag} 경로가 없습니다: {path}")
    if app.suffix != suffix or app.is_dir() != (device == "ios"):
        kind = "시뮬레이터 빌드 .app 폴더" if device == "ios" else ".apk 파일"
        raise DeviceQAError(f"{file_flag}는 {kind}여야 합니다(.ipa·실기기 빌드는 범위 밖): {path}")
    if device == "android":
        return {"id": None, "source": "app", "path": str(app)}
    info = _app_info(app)
    if not info.get("CFBundleIdentifier"):
        raise DeviceQAError(f"{path}/Info.plist에서 CFBundleIdentifier를 읽지 못했습니다")
    return {"id": info["CFBundleIdentifier"], "source": "app", "path": str(app), "executable": info.get("CFBundleExecutable")}


def _app_info(app):
    """Info.plist of a simulator .app; {} when it cannot be read."""
    try:
        with open(Path(app) / "Info.plist", "rb") as plist:
            info = plistlib.load(plist)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}
    return info if isinstance(info, dict) else {}


def _apk_package(run, root, apk):
    """The package name inside an .apk (newest build-tools' aapt2); None when there is no aapt2 — shown, not needed."""
    tools = sorted((Path(root) / "build-tools").glob("*/aapt2"),
                   key=lambda t: [int(x) if x.isdigit() else 0 for x in t.parent.name.split(".")])
    if not tools:
        return None
    return run([tools[-1], "dump", "packagename", apk]).strip() or None


def _installed_app(run, device, target, spec, root=None):
    """An installed app (`--bundle-id`/`--package`) must be on the device; a `--apk` gets its package name."""
    if spec is None:
        return None
    if spec["source"] == "app":
        if device == "android" and spec["id"] is None:
            spec = {**spec, "id": _apk_package(run, root, spec["path"])}
        return spec
    if device == "ios":
        container = run(["xcrun", "simctl", "get_app_container", target, spec["id"]], check=False).strip()
        installed = bool(container)
        spec = {**spec, "executable": _app_info(container).get("CFBundleExecutable") if container else None}
    else:
        adb = str(Path(root) / "platform-tools/adb")
        installed = run([adb, "-s", target, "shell", "pm", "path", spec["id"]], check=False).strip().startswith("package:")
    if not installed:
        raise DeviceQAError(f"{spec['id']}는 {target}에 설치되지 않았습니다 — `{APP_FLAGS[device][0]}`으로 여세요. "
                            f"`{device} down` 뒤 다시")
    return spec


def _caps(device, target, spec, auto_grant=True):
    """The one place where web and app mode differ when a session opens."""
    if device == "ios":
        caps = {"platformName": "iOS", "appium:automationName": "XCUITest", "appium:udid": target,
                "appium:newCommandTimeout": 600, **IOS_CAPS}
    else:
        caps = {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:udid": target,
                "appium:newCommandTimeout": 600,
                "appium:autoGrantPermissions": bool(auto_grant)}
    if spec is None:
        caps["browserName"] = "Safari" if device == "ios" else "Chrome"
    elif spec["source"] == "app":
        caps["appium:app"] = spec["path"]
    elif device == "ios":
        caps["appium:bundleId"] = spec["id"]
    else:
        caps.update({"appium:appPackage": spec["id"], "appium:appActivity": spec["activity"]})
    if spec and device == "ios" and spec.get("executable"):
        # device run: the simulator lists the app to the web inspector as `process-<executable>`, not its bundle
        # id, so after the first switch Appium found no page and `contexts` lost the web view
        caps["appium:additionalWebviewBundleIds"] = [f"process-{spec['executable']}"]
    return caps


def _open_session(lock_dir, device, owner, port, target, spec, auto_grant=True):
    session = webdriver("POST", f"{_server(port)}/session",
                        {"capabilities": {"alwaysMatch": _caps(device, target, spec, auto_grant)}}, timeout=900)
    fields = {"session_id": _session_id(session)}
    if spec:
        fields.update(mode="app", app={k: spec[k] for k in ("id", "source", "path") if k in spec})
    return update(lock_dir, device, owner, **fields)


def _context_names(contexts):
    return [c for c in contexts if isinstance(c, str)] if isinstance(contexts, list) else []


def _enter_webview(lock, timeout=WEBVIEW_WAIT_S):
    """Wait for the app's web view and go into it; without one the session stays NATIVE_APP and says so."""
    session, end = _session(lock), time.time() + timeout
    while True:
        contexts = webdriver("GET", f"{session}/contexts")
        webview = next((c for c in _context_names(contexts) if c.startswith("WEBVIEW")), None)
        if webview:
            webdriver("POST", f"{session}/context", {"name": webview})
            return {"context": webview, "contexts": contexts}
        if time.time() >= end:
            return {"context": webdriver("GET", f"{session}/context"), "contexts": contexts, "webview": f"없음 {timeout}초"}
        time.sleep(0.5)


def _web_ready(lock_dir, owner, device="ios"):
    """The one context check: page commands in app mode run only inside a web view."""
    lock = _ready(lock_dir, owner, device)
    if lock.get("mode") == "app":
        current = webdriver("GET", f"{_session(lock)}/context")
        if not (isinstance(current, str) and current.startswith("WEBVIEW")):
            raise DeviceQAError(f"웹뷰가 아닙니다 — `{device} context WEBVIEW` 뒤 다시(지금 {current!r})")
    return lock


def _app_ready(lock_dir, owner, device, command):
    lock = _ready(lock_dir, owner, device)
    if lock.get("mode") != "app":
        file_flag, id_flag, _ = APP_FLAGS[device]
        raise DeviceQAError(f"웹 모드 세션에는 {command}가 없습니다 — 앱 모드(`{device} up {file_flag}`·`{id_flag}`)에서만")
    return lock


def ios_context(lock_dir, owner, name=None, device="ios"):
    """No name: where the session is. NATIVE_APP, WEBVIEW (the first WEBVIEW_*) or an exact name: go there."""
    lock = _app_ready(lock_dir, owner, device, "context")
    session = _session(lock)
    contexts = webdriver("GET", f"{session}/contexts")
    names = _context_names(contexts)
    if name is None:
        return {"current": webdriver("GET", f"{session}/context"), "contexts": contexts}
    target = next((c for c in names if c.startswith("WEBVIEW")), None) if name == "WEBVIEW" else (name if name in names else None)
    if target is None:
        what = "웹뷰가 없습니다" if name == "WEBVIEW" else f"없는 문맥입니다: {name}"
        raise DeviceQAError(f"{what}. 있는 것: {', '.join(names) or '없음'}(원문 {contexts!r})")
    webdriver("POST", f"{session}/context", {"name": target})
    return {"current": target, "contexts": contexts}


def ios_push(lock_dir, owner, payload, run=run):
    """A push notification to the app under test (simulator only, `simctl push`)."""
    lock = _app_ready(lock_dir, owner, "ios", "push")
    if not Path(payload).is_file():
        raise DeviceQAError(f"payload 파일이 없습니다: {payload}")
    run(["xcrun", "simctl", "push", lock["udid"], lock["app"]["id"], payload])
    return {"pushed": payload, "app": lock["app"]["id"]}


def _record(lock_dir, owner, action, path, device, run, sdk):
    lock = own(lock_dir, device, owner)
    target = lock.get("udid" if device == "ios" else "serial")
    if not target:
        raise DeviceQAError(f"켜진 기기가 없는 잠금입니다. 먼저 `{device} up --owner {owner}`")
    if action == "stop":
        return _stop_recording(lock_dir, owner, device, run, sdk)
    if lock.get("recording"):
        raise DeviceQAError(f"이미 녹화 중입니다({lock['recording']['path']}) — `{device} record stop` 먼저")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(path).expanduser().absolute() if path else Path(lock_dir).absolute() / f"{device}-record-{stamp}.mp4"
    if not out.parent.is_dir():
        raise DeviceQAError(f"녹화 파일을 둘 폴더가 없습니다: {out.parent}")
    log = Path(lock_dir) / f"{device}-record.log"
    if device == "ios":
        recording = {"pid": spawn(["xcrun", "simctl", "io", target, "recordVideo", "--force", str(out)], log), "path": str(out)}
    else:
        remote = f"/sdcard/device_qa-{stamp}.mp4"
        adb = str(find_sdk(sdk) / "platform-tools/adb")
        cmd = [adb, "-s", target, "shell", "screenrecord", "--time-limit", str(RECORD_LIMIT_S), remote]
        recording = {"pid": spawn(cmd, log), "path": str(out), "remote": remote}
    update(lock_dir, device, owner, recording=recording)
    return {"recording": str(out)}


def _stop_recording(lock_dir, owner, device, run, sdk, timeout=10):
    """Stop, wait for the recorder to finish its file, then forget it in the lock; an empty file is an error."""
    lock = own(lock_dir, device, owner)
    recording = lock.get("recording")
    if not recording:
        raise DeviceQAError(f"녹화 중이 아닙니다 — `{device} record start` 먼저")
    marker = "recordVideo" if device == "ios" else "screenrecord"
    if device == "ios":
        interrupt_pid(recording["pid"])
    else:
        adb = str(find_sdk(sdk) / "platform-tools/adb")
        run([adb, "-s", lock["serial"], "shell", "pkill", "-INT", "-f", recording["remote"]], check=False)  # only mine (N3)
    end = time.time() + timeout
    while marker in process_command(recording["pid"]):
        if time.time() > end:
            raise DeviceQAError(f"녹화가 {timeout}초 안에 멈추지 않았습니다(PID {recording['pid']}). 다시 `{device} record stop`")
        time.sleep(0.25)
    if device == "android":
        try:
            run([adb, "-s", lock["serial"], "pull", recording["remote"], recording["path"]])
        except DeviceQAError as error:  # the mark goes, so the next `down` is not blocked
            _forget_recording(lock_dir, device, owner)
            raise DeviceQAError(f"녹화를 가져오지 못했습니다(기기 쪽 {recording['remote']}, 남아 있을 수 있음): {error}. "
                                "녹화 표시는 지웠습니다") from error
        run([adb, "-s", lock["serial"], "shell", "rm", "-f", recording["remote"]], check=False)
    _forget_recording(lock_dir, device, owner)
    out = Path(recording["path"])
    size = out.stat().st_size if out.is_file() else 0
    if not size:
        raise DeviceQAError(f"녹화 파일이 없습니다: {out} (로그 {Path(lock_dir) / (device + '-record.log')})")
    return {"path": recording["path"], "bytes": size}


def _forget_recording(lock_dir, device, owner):
    lock = own(lock_dir, device, owner)
    lock.pop("recording", None)
    _write(_lock_path(lock_dir, device), lock)


def _down_recording(lock_dir, owner, device, run, sdk):
    """`down` stops a recording first and is never blocked by it: a failure is reported in the
    result, the mark is cleared, and session → server → device → lock still follow."""
    if not own(lock_dir, device, owner).get("recording"):
        return None
    try:
        return {"recording": _stop_recording(lock_dir, owner, device, run, sdk)}
    except DeviceQAError as error:
        _forget_recording(lock_dir, device, owner)
        return {"recording_error": str(error)}


def ios_record(lock_dir, owner, action, path=None, run=run):
    return _record(lock_dir, owner, action, path, "ios", run, None)


def _appium_server(lock_dir, device, owner, port, log=None, extra_env=None, extra_args=()):
    """Start our Appium server for this device's lock and wait for it; the PID is in the lock before the wait so
    `down` can stop it after a failure. Returns the time the server answered."""
    flag = "--appium-port" if device == "android" else "--port"  # android's --port is the emulator's
    if _port_in_use(port):
        raise DeviceQAError(f"포트 {port}는 이미 사용 중입니다(다른 세션이나 다른 서버). 끄지 말고 {flag}로 다른 포트")
    appium = find_appium()
    env = {**os.environ, **(extra_env or {}), "PATH": f"{Path(appium).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    log = log or Path(lock_dir) / f"{device}-appium.log"
    pid = spawn([appium, "server", "--port", str(port), "--log", str(log), *extra_args], log, env)
    update(lock_dir, device, owner, server_pid=pid, port=port)
    end = time.time() + 60
    while not _server_alive(port):
        if time.time() > end:
            raise DeviceQAError(f"Appium 서버가 {port}에서 열리지 않았습니다. `{device} down` 후 로그 확인")
        time.sleep(0.5)
    return time.time()


def _session_id(session):
    """Appium's session answer must carry a sessionId; anything else is reported, not a TypeError."""
    if not isinstance(session, dict) or not session.get("sessionId"):
        raise DeviceQAError(f"Appium이 세션 ID 대신 {session!r}를 돌려줬습니다. `down` 후 로그 확인")
    return session["sessionId"]


def _safari_tabs(lock):
    """How many Safari pages (tabs) the session sees; None when it cannot be read — a count is not a reason to fail
    `up`. Tabs are not closed: a warm device keeps them like it keeps cookies (`ios reset` empties both)."""
    try:
        handles = webdriver("GET", f"{_session(lock)}/window/handles")
    except DeviceQAError:
        return None
    return len(handles) if isinstance(handles, list) else None


def _ready(lock_dir, owner, device="ios"):
    lock = own(lock_dir, device, owner)
    if not lock.get("session_id") or not lock.get("port"):
        raise DeviceQAError(f"`{device} up`이 끝나지 않은 잠금입니다. `{device} down --owner {owner}` 후 다시 up")
    return lock


def _session(lock):
    return f"{_server(lock['port'])}/session/{lock['session_id']}"


def _same_url(a, b):
    return a.split("#")[0].rstrip("/") == b.split("#")[0].rstrip("/")


def wait_loaded(port, session_id, url, timeout=30):
    """Wait until the page is `url` and fully loaded; report instead of failing (redirects are QA data)."""
    end, href = time.time() + timeout, None
    while time.time() < end:
        try:
            href, ready = webdriver("POST", f"{_server(port)}/session/{session_id}/execute/sync",
                                    {"script": "return [location.href, document.readyState];", "args": []})
            if ready == "complete" and _same_url(href, url):
                return {"loaded": True, "href": href}
        except (TypeError, ValueError):  # an odd reply while the page changes; Appium errors are raised
            pass
        time.sleep(0.5)
    return {"loaded": False, "href": href}


def ios_open(lock_dir, owner, url, run=run, device="ios"):
    lock = _web_ready(lock_dir, owner, device)
    webdriver("POST", f"{_session(lock)}/url", {"url": url}, timeout=120)
    return wait_loaded(lock["port"], lock["session_id"], url)


def ios_eval(lock_dir, owner, expression=None, script=None, run=run, device="ios"):
    """One expression (`return (EXPR);`) or a script run as a function body as is."""
    if (expression is None) == (script is None):
        raise DeviceQAError("EXPR와 --script 가운데 하나만 줍니다")
    lock = _web_ready(lock_dir, owner, device)
    body = script if script is not None else f"return ({expression});"
    return webdriver("POST", f"{_session(lock)}/execute/sync", {"script": body, "args": []})


def _element(lock, selector=None):
    if selector is None:
        found = webdriver("GET", f"{_session(lock)}/element/active")
    else:
        found = webdriver("POST", f"{_session(lock)}/element", {"using": "css selector", "value": selector})
    if not found:
        raise DeviceQAError(f"요소 없음: {selector or '포커스된 요소'}")
    return next(iter(found.values()))


# Page scripts are tagged `/*dq:<tag>*/` so the Appium log and the tests can tell them apart from `eval`.
_DESCRIBE = ("function d(n){if(!n||!n.tagName)return null;var s=n.tagName.toLowerCase();if(n.id)s+='#'+n.id;"
             "var m=n.getAttribute('name');if(m)s+='[name='+m+']';var t=(n.innerText||n.value||'').trim().slice(0,20);"
             "return t?s+' \"'+t+'\"':s;}")
_VIEWPORT = "var v=window.visualViewport||{width:innerWidth,height:innerHeight,offsetTop:0,offsetLeft:0,scale:1};"
_SCRIPTS = {
    "blur": "var a=document.activeElement;if(a&&a!==document.body&&(a.isContentEditable||"
            "/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))){a.blur();return true;}return false;",
    "scroll": "arguments[0].scrollIntoView({block:'center',inline:'center'});return true;",
    "state": _VIEWPORT + "var r=arguments[0].getBoundingClientRect();return [innerWidth,innerHeight,v.width,v.height,"
             "v.offsetTop,v.offsetLeft,v.scale,scrollX,scrollY,r.left,r.top,r.width,r.height].join(',');",
    # the centre in layout-viewport coordinates must lie inside the visual viewport (keyboard: offsetTop, height)
    "measure": _DESCRIBE + _VIEWPORT + "var e=arguments[0],r=e.getBoundingClientRect(),x=r.left+r.width/2,"
               "y=r.top+r.height/2,at=document.elementFromPoint(x,y),l=e.closest('a[href]');return {x:Math.round(x),"
               "y:Math.round(y),w:r.width,h:r.height,visible:x>=v.offsetLeft&&x<=v.offsetLeft+v.width&&y>=v.offsetTop&&"
               "y<=v.offsetTop+v.height,at:d(at),covered:!(at&&(at===e||e.contains(at))),link:l?l.href:null,"
               "href:location.href,zoom:v.scale};",  # a `scale` key is dropped on the way back (device run)
    # every touch the page sees until the next tap; kept in sessionStorage too so a same-origin page change keeps it
    "record": _DESCRIBE + "var e=arguments[0],tok=arguments[1],k='__device_qa_tap',T=['touchstart','pointerdown','click'];"
              "if(window[k])T.forEach(function(t){document.removeEventListener(t,window[k],true)});var h=[];"
              "function save(){try{sessionStorage.setItem(k,JSON.stringify({token:tok,hits:h}));return null}"
              "catch(x){return String(x&&x.name||x)}}"
              "window[k]=function(ev){var p=ev.touches&&ev.touches[0]||ev;h.push({type:ev.type,x:Math.round(p.clientX),"
              "y:Math.round(p.clientY),inside:!!ev.target&&(ev.target===e||e.contains(ev.target)),target:d(ev.target)});"
              "save();};window[k+'_hits']=h;window[k+'_token']=tok;"
              "T.forEach(function(t){document.addEventListener(t,window[k],true)});var err=save();"
              "return {stored:!err,error:err};",
    "after": "var k='__device_qa_tap',tok=arguments[0],r=null,s=null;try{r=JSON.parse(sessionStorage.getItem(k)||'null')}"
             "catch(x){s=String(x&&x.name||x)}var h=r&&r.token===tok?r.hits:(window[k+'_token']===tok?window[k+'_hits']:null);"
             "return {href:location.href,hits:h,store:s};",
    "jsclick": "arguments[0].click();return location.href;",
    # the page body is not a focused field (same rule as `blur`)
    "focused": "var e=arguments[0];return e===document.activeElement&&e!==document.body&&e!==document.documentElement;",
    "read": "var e=arguments[0];return e.value===undefined?e.textContent:e.value;",
}


def _js(lock, tag, *args):
    return webdriver("POST", f"{_session(lock)}/execute/sync", {"script": f"/*dq:{tag}*/{_SCRIPTS[tag]}", "args": list(args)})


def _settle(lock, ref, timeout=SETTLE_TIMEOUT):
    """Wait until the viewport, scroll and element box read the same SETTLE_READS times in a row (keyboard and
    scroll animations), so the touch is placed on a still screen."""
    end, seen = time.time() + timeout, []
    while True:
        seen = (seen + [_js(lock, "state", ref)])[-SETTLE_READS:]
        if len(seen) == SETTLE_READS and len(set(seen)) == 1:
            return seen[-1]
        if time.time() > end:
            raise DeviceQAError(f"화면이 {timeout}초 안에 멈추지 않았습니다(스크롤·키보드 움직임). 누르지 않았습니다: {seen[-1]}")
        time.sleep(0.15)


def judge_tap(before, after, device="ios"):
    """The first touch the page saw decides: on the element (or inside it) is a hit, elsewhere is a miss even if a
    click is passed on to the element later (label, cover). With no record, a link that opened its
    own address is a hit (a new origin loses the record); anything else is a miss, reported with where it went."""
    hits, href = after.get("hits"), after.get("href") or ""
    result = {"hit": True, "href": after.get("href"), "at": before.get("at"), "event": hits[0] if hits else None}
    store = after.get("store") or before.get("store_error")
    if hits:
        first = hits[0]
        if first.get("inside"):
            return result
        reason = f"다른 요소가 눌렸습니다: {first.get('target')} ({first.get('x')},{first.get('y')})"
    elif not _same_url(href, before.get("href") or ""):
        if before.get("link") and _same_url(href, before["link"]):
            return result
        reason = f"예상 못 한 이동: {href}" + (" (터치 기록 없음)" if hits is None else "")
    elif hits is None:
        reason = "터치 기록을 잃었습니다(같은 주소로 다시 읽혔거나 페이지가 sessionStorage를 쓰지 못함" + \
                 (f": {store}" if store else "") + ")"
    else:
        browser = "Safari" if device == "ios" else "Chrome"
        reason = f"웹 페이지에 터치가 오지 않음 — 주소창 등 {browser} 화면을 눌렀을 수 있습니다(`{device} dismiss` 뒤 다시)"
    raise DeviceQAError(f"빗나감: {reason}. 요소 가운데 ({before.get('x')},{before.get('y')}), 그 자리 요소 {before.get('at')}")


def _native_tap(lock, element):
    """A real touch placed on a still screen with no open keyboard, then checked on the page.
    No retries: a miss is an error."""
    ref = {W3C_ELEMENT: element}
    if _js(lock, "blur"):  # a keyboard that opens or closes during Appium's calibration taps spoils it for the session
        _settle(lock, ref)
    _js(lock, "scroll", ref)
    _settle(lock, ref)
    before = _js(lock, "measure", ref)
    if not isinstance(before, dict):
        raise DeviceQAError(f"측정값을 읽지 못했습니다({before!r}). 누르지 않았습니다")
    missing = [k for k in ("x", "y", "w", "h", "visible", "covered", "href", "zoom") if before.get(k) is None]
    zoom = before.get("zoom")
    if "zoom" not in missing and (isinstance(zoom, bool) or not isinstance(zoom, (int, float)) or zoom <= 0):
        missing.append("zoom")
    if missing:  # a key Appium did not send back is not taken as safe (device run ②)
        raise DeviceQAError(f"측정값을 읽지 못했습니다({', '.join(missing)}: {before}). 누르지 않았습니다")
    if not before.get("w") or not before.get("h"):
        raise DeviceQAError(f"보이지 않는 요소입니다(크기 0). 누르지 않았습니다: {before.get('at')}")
    scale = zoom
    if abs(scale - 1) > 0.01:  # device run: zoomed by an input, Appium's calibration missed by 164 px every time
        raise DeviceQAError(f"페이지가 확대돼 있습니다(visualViewport.scale {scale:.2f}, 입력칸 확대 등). 이 상태의 실제 터치는 "
                            "Appium 좌표 보정이 맞지 않아 누르지 않았습니다 — 터치가 확인 대상이 아니면 `tap --js`, "
                            "아니면 `open`으로 다시 열기")
    if not before.get("visible"):
        raise DeviceQAError(f"스크롤해도 요소 가운데 ({before.get('x')},{before.get('y')})가 화면 안에 들어오지 않습니다. "
                            f"누르지 않았습니다. 브라우저 화면(주소 입력·암호 저장)이 덮었으면 `{lock.get('device', 'ios')} dismiss`")
    if before.get("covered"):  # a real touch would land on what is on top
        raise DeviceQAError(f"요소 가운데가 다른 요소에 가려져 있습니다: 그 자리 요소 {before.get('at')}. 누르지 않았습니다 — "
                            "보이는 그 요소를 지정하거나 터치가 확인 대상이 아니면 `tap --js`")
    token = os.urandom(8).hex()
    recorded = _js(lock, "record", ref, token)
    if isinstance(recorded, dict) and recorded.get("error"):
        before["store_error"] = recorded["error"]
    if lock.get("device") == "android":  # a touch pointer at the measured centre (chromedriver W3C actions)
        _touch_at(lock, before["x"], before["y"])
    else:
        webdriver("POST", f"{_session(lock)}/element/{element}/click", {})
    end = time.time() + HIT_TIMEOUT
    while True:
        after = _js(lock, "after", token)
        if not isinstance(after, dict):  # was a TypeError traceback
            raise DeviceQAError(f"터치 뒤 페이지 기록을 읽지 못했습니다(Appium 응답 {after!r}). 눌렀을 수 있습니다 — eval로 확인")
        if after.get("hits") or not _same_url(after.get("href") or "", before.get("href") or "") or time.time() > end:
            return judge_tap(before, after, lock.get("device", "ios"))
        time.sleep(0.2)


def _touch_at(lock, x, y):
    """One finger down and up at viewport CSS coordinates — what chromedriver takes for a touch pointer."""
    finger = {"type": "pointer", "id": "finger", "parameters": {"pointerType": "touch"},
              "actions": [{"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)}, {"type": "pointerDown", "button": 0},
                          {"type": "pause", "duration": 50}, {"type": "pointerUp", "button": 0}]}
    webdriver("POST", f"{_session(lock)}/actions", {"actions": [finger]})


def ios_tap(lock_dir, owner, selector, js=False, run=run, device="ios"):
    lock = _web_ready(lock_dir, owner, device)
    element = _element(lock, selector)
    if js:  # a sure click for steps that are not about touch
        return {"js": True, "href": _js(lock, "jsclick", {W3C_ELEMENT: element})}
    try:
        result = _native_tap(lock, element)
    except DeviceQAError as error:
        warning = load_warning()  # misses came with a busy Mac
        if warning:
            raise DeviceQAError(f"{error} ({warning})") from error
        raise
    warning = load_warning()
    return {**result, "load_warning": warning} if warning else result


def ios_type(lock_dir, owner, text, selector=None, clear=False, run=run, device="ios"):
    """Type into the field; a field that is not focused is tapped (checked) first, a focused one is not touched
    again — Appium's `value` would tap it once more at a stale spot."""
    lock = _web_ready(lock_dir, owner, device)
    element = _element(lock, selector)
    ref, tapped = {W3C_ELEMENT: element}, False
    if not _js(lock, "focused", ref):
        if selector is None:  # never type into the page body
            raise DeviceQAError("포커스된 입력칸이 없습니다. `--selector`로 칸을 지정하세요. 입력하지 않았습니다")
        _native_tap(lock, element)
        tapped = True
        if not _js(lock, "focused", ref):
            raise DeviceQAError(f"탭은 맞았지만 포커스가 가지 않았습니다: {selector or '포커스된 요소'}. 입력하지 않았습니다")
    if device != "ios":  # chromedriver's `value` types into the focused field; there is no native-tap setting to park
        if clear:
            webdriver("POST", f"{_session(lock)}/element/{element}/clear", {})
        webdriver("POST", f"{_session(lock)}/element/{element}/value", {"text": text})
        return {"value": _js(lock, "read", ref), "tapped": tapped}
    settings = f"{_session(lock)}/appium/settings"
    webdriver("POST", settings, {"settings": {"nativeWebTap": False}})
    try:
        if clear:
            webdriver("POST", f"{_session(lock)}/element/{element}/clear", {})
        webdriver("POST", f"{_session(lock)}/element/{element}/value", {"text": text})
    except Exception as error:  # a failed restore must not hide why typing failed
        try:
            webdriver("POST", settings, {"settings": {"nativeWebTap": True}})
        except DeviceQAError as back:
            raise DeviceQAError(f"{error} — 그리고 실제 터치 설정을 되돌리지 못했습니다({back}). "
                                "다음 tap이 합성 클릭일 수 있으니 `ios down` 뒤 다시 up") from error
        raise
    try:
        webdriver("POST", settings, {"settings": {"nativeWebTap": True}})
    except DeviceQAError as back:
        raise DeviceQAError(f"입력은 했지만 실제 터치 설정을 되돌리지 못했습니다({back}). "
                            "다음 tap이 합성 클릭일 수 있으니 `ios down` 뒤 다시 up")
    return {"value": _js(lock, "read", ref), "tapped": tapped}


def _native_action(lock_dir, owner, action, labels=None, run=run, device="ios"):
    """Close a browser screen over the page by tapping its button in the native tree (buttons inside the page are
    left out), then go back to the page context. Odd Appium answers are errors, not "nothing to close", and a
    failed way back is added to the first error instead of hiding it."""
    lock = _ready(lock_dir, owner, device)
    session = _session(lock)
    context = webdriver("GET", f"{session}/context")
    if not isinstance(context, str) or not context:
        raise DeviceQAError(f"지금 웹 문맥을 읽지 못했습니다({context!r}). 돌아올 곳이 없어 NATIVE_APP으로 바꾸지 않았습니다")
    button_type, attr, default_labels, webview = NATIVE_BUTTONS[device]
    # Inspection is read-only and must enumerate every native button, including labels
    # that are not in our known allow/deny vocabulary.
    wanted = tuple() if action == "inspect" and labels is None else tuple(labels or default_labels)
    predicates = " or ".join(f"@{attr}='{label}'" for label in wanted)
    query = f"//{button_type}[({predicates}) and not(ancestor::{webview})]" if predicates else f"//{button_type}[not(ancestor::{webview})]"
    webdriver("POST", f"{session}/context", {"name": "NATIVE_APP"})
    try:
        found = webdriver("POST", f"{session}/elements", {
            "using": "xpath", "value": query})
        if not isinstance(found, list):
            raise DeviceQAError(f"Appium이 버튼 목록 대신 {found!r}를 돌려줬습니다")
        buttons = {webdriver("GET", f"{session}/element/{ref}/attribute/{attr}"): ref
                   for ref in (next(iter(f.values())) for f in found)}
        visible = [label for label in buttons if isinstance(label, str) and label]
        if action == "inspect":
            result = {"context": context, "buttons": visible}
        else:
            label = next((label for label in wanted if label in buttons), None)
            if not label:
                if action == "close":
                    result = {"dismissed": None}
                else:
                    raise DeviceQAError(f"처리할 네이티브 버튼이 없습니다({action}). 현재 버튼: {', '.join(visible) or '없음'}")
            else:
                webdriver("POST", f"{session}/element/{buttons[label]}/click", {})
                result = {"dismissed": label} if action == "close" else {"permission": action, "pressed": label}
    except Exception as error:  # any failure, odd answer shapes too, goes back to the page first
        try:
            webdriver("POST", f"{session}/context", {"name": context})
        except DeviceQAError as back:
            raise DeviceQAError(f"{error} — 그리고 웹 문맥으로 돌아가지 못했습니다({back}). `{device} down` 뒤 다시 up") from error
        raise
    webdriver("POST", f"{session}/context", {"name": context})
    return result


def ios_dismiss(lock_dir, owner, run=run, device="ios"):
    """Close a known browser/permission screen without ever accepting a permission."""
    return _native_action(lock_dir, owner, "close", run=run, device=device)


def permission(lock_dir, owner, resource, action, run=run, device="ios"):
    if resource not in PERMISSION_LABELS:
        raise DeviceQAError(f"알 수 없는 권한입니다: {resource}. 가능한 값: {', '.join(sorted(PERMISSION_LABELS))}")
    if action not in {"allow", "deny", "close", "inspect"}:
        raise DeviceQAError("권한 동작은 allow·deny·close·inspect 중 하나여야 합니다")
    labels = None if action == "inspect" else PERMISSION_LABELS[resource].get(action)
    if action == "close":
        labels = NATIVE_BUTTONS[device][2]
    result = _native_action(lock_dir, owner, action, labels=labels, run=run, device=device)
    if action != "inspect":
        result["resource"] = resource
        result["action"] = action
    return result


def native_inspect(lock_dir, owner, run=run, device="ios"):
    return _native_action(lock_dir, owner, "inspect", run=run, device=device)


def ios_shot(lock_dir, owner, path, run=run):
    lock = _ready(lock_dir, owner)
    Path(path).write_bytes(base64.b64decode(webdriver("GET", f"{_session(lock)}/screenshot")))


def _is_our_server(command, port):
    """`appium` on the command line and exactly `--port <port>` / `--port=<port>` (not 47910)."""
    tokens = command.split()
    if not any("appium" in Path(token).name for token in tokens):
        return False
    return any(t == f"--port={port}" or (t == "--port" and i + 1 < len(tokens) and tokens[i + 1] == str(port))
               for i, t in enumerate(tokens))


def _stop(pid, still_ours, timeout=5, device="ios"):
    """SIGTERM a process this tool started and wait for it; report (keeping the lock) instead of guessing."""
    try:
        kill_pid(pid)
    except PermissionError as error:
        raise DeviceQAError(f"PID {pid}을 끌 권한이 없습니다({error}). 잠금을 남깁니다 — 사람이 확인")
    end = time.time() + timeout
    while still_ours(process_command(pid)):
        if time.time() > end:
            raise DeviceQAError(f"PID {pid}이 {timeout}초 안에 끝나지 않았습니다. 잠금을 남깁니다 — 다시 `{device} down`")
        time.sleep(0.25)


def ios_down(lock_dir, owner, run=run):
    """Session first, then our server, then only the WebDriverAgent build marked for our device (spike: it outlives
    the server). The device is kept for the next session."""
    lock = own(lock_dir, "ios", owner)
    stopped = _down_recording(lock_dir, owner, "ios", run, None)
    if lock.get("session_id") and lock.get("port"):
        try:
            webdriver("DELETE", _session(lock), timeout=120)
        except DeviceQAError:
            pass
    pid, port = lock.get("server_pid"), lock.get("port")
    if pid and _is_our_server(process_command(pid), port):
        _stop(pid, lambda command: _is_our_server(command, port))
    udid = lock.get("udid")
    if udid:
        try:
            processes = run(["ps", "-ax", "-o", "pid=,command="])
        except DeviceQAError as error:
            raise DeviceQAError(f"ps 실패로 WebDriverAgent 정리를 확인하지 못했습니다({error}). 잠금을 남깁니다")
        for line in processes.splitlines():
            if "xcodebuild" in line and WDA_MARKER in line and f"id={udid}" in line:
                _stop(int(line.split()[0]), lambda command: "xcodebuild" in command)
    release(lock_dir, "ios", owner)
    return stopped


def ios_reset(lock_dir, owner, run=run, device_type="iPhone 17"):
    acquire(lock_dir, "ios", owner)
    lock = own(lock_dir, "ios", owner)
    if lock.get("session_id") or lock.get("server_pid"):
        raise DeviceQAError(f"세션이 켜져 있습니다. 먼저 `ios down --owner {owner}`")
    try:
        udid = lock.get("udid")
        devices = _devices(run)
        target = [d for d in devices if d["udid"] == udid] if udid else [d for d in devices if d["name"] == _qa_name(device_type)]
        if not target or not target[0]["name"].startswith(QA_PREFIX):
            raise DeviceQAError(f"지울 {QA_PREFIX} 기기가 없습니다. 이름이 {QA_PREFIX}로 시작하는 기기만 비웁니다")
        run(["xcrun", "simctl", "shutdown", target[0]["udid"]], check=False)
        run(["xcrun", "simctl", "erase", target[0]["udid"]])
    finally:
        release(lock_dir, "ios", owner)

# ---- Android -----------------------------------------------------------------------------------------------

def find_sdk(sdk=None):
    candidates = [sdk] if sdk else [os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME"), *SDK_CANDIDATES]
    for candidate in filter(None, candidates):
        root = Path(candidate).expanduser()
        if (root / "emulator/emulator").exists() and (root / "platform-tools/adb").exists():
            return root
    raise DeviceQAError("Android SDK(emulator·platform-tools)를 찾지 못했습니다. --sdk 또는 ANDROID_SDK_ROOT")


def _device_is_mine(lock):
    """The one ownership rule: mine = an emulator this tool started
    (`started: true`) or the iOS QA simulator. `reused`, `started: false` and an old lock without the key are
    someone else's — the safe side when it is not known."""
    return lock.get("device") == "ios" or lock.get("started") is True


def _state_change_refusal(device, state, mine, target, wipe=False, avd=None, app=False):
    """The entry-state table: may this `up` apply arguments that change the device (`--wipe`, another `--avd`, app
    arguments) in this entry state on this device? Returns the refusal, or None. States: "session" (a session is
    open), "serial" (a device is in the lock, no session), "running" (no device in the lock, an emulator is on),
    "fresh" (nothing — this `up` starts it)."""
    changes = [what for what, given in (("--wipe", wipe), (f"--avd {avd}", avd), ("앱 인자", app)) if given]
    if not changes or state == "fresh":
        return None
    first = changes[0]
    if state == "session":
        return f"세션이 열려 있습니다({target}, {first}는 새로 켤 때만). `{device} down` 뒤 다시"
    if mine:  # "serial" on my own device: app arguments are fine, the start-only ones are not
        if first == "앱 인자":
            return None
        return f"{first}는 새로 켤 때만: 이미 켜진 {target}이 잠금에 있습니다. `{device} down` 뒤 다시"
    if first == "--wipe":
        return (f"--wipe를 할 수 없습니다: 이미 켜진 에뮬레이터 {target}를 쓰게 됩니다(끄지 않음). "
                "깨끗한 QA AVD가 필요하면 그 에뮬레이터 주인에게 묻거나 기다린 뒤 다시")
    if first.startswith("--avd"):
        return (f"{first}를 켤 수 없습니다: 켜진 에뮬레이터 {target}를 쓰게 됩니다(끄지 않음). "
                "그 AVD가 필요하면 그 에뮬레이터 주인에게 묻거나 기다린 뒤 다시")
    return f"켜진 에뮬레이터 {target}는 남의 것이라 앱을 설치하거나 데이터를 지우지 않습니다 — 기다린 뒤 다시"


def android_up(lock_dir, owner, run=run, sdk=None, avd=QA_AVD, window=False, port=5584, log=None, url=None,
               appium_port=APPIUM_PORT, wipe=False, apk=None, package=None, activity=None, auto_grant=True):
    """Emulator (ours or one already running) → our Appium server → UiAutomator2 Chrome session.
    The default AVD is the QA one `setup` creates; a user's AVD is used only when named, and never wiped."""
    spec = _app_spec("android", url, apk, package, activity)  # before any lock: a wrong combination changes nothing
    root = find_sdk(sdk)
    adb = str(root / "platform-tools/adb")
    started_at = time.time()
    if wipe and not avd.startswith(QA_PREFIX):
        raise DeviceQAError(f"--wipe는 {QA_PREFIX} AVD만: {avd}은 사용자 AVD라 지우지 않습니다")
    to_stop, fresh = [], not _already_up(lock_dir, "android", owner, "serial")
    if fresh:
        _, to_stop = _gate(lock_dir, "android", run)
    acquire(lock_dir, "android", owner)
    if fresh:
        _locked_after_gate(lock_dir, "android", owner)
    lock = own(lock_dir, "android", owner)
    # Entry-state table: --wipe and --avd mean something only when this `up`
    # starts the emulator, so every other state refuses them instead of ignoring them. The lock was already ours: kept.
    changes = {"wipe": wipe, "avd": avd if avd != QA_AVD else None, "app": spec is not None}
    if lock.get("session_id"):  # already up: an `up --url` still opens the URL, like `open`
        refusal = _state_change_refusal("android", "session", _device_is_mine(lock), lock["serial"], **changes)
        if refusal:
            raise DeviceQAError(refusal)
        return {**lock, **android_open(lock_dir, owner, url)} if url else lock
    if lock.get("serial"):  # an older lock or a failed session: the emulator is there, open the session
        mine = _device_is_mine(lock)
        refusal = _state_change_refusal("android", "serial", mine, lock["serial"], **changes)
        if refusal:
            if not mine:  # let go of someone else's emulator the way `down` does (our server too, not theirs)
                android_down(lock_dir, owner, run, sdk)
            raise DeviceQAError(refusal)
        return _android_session(lock_dir, owner, root, lock, started_at, started_at, log, url, appium_port, run, spec,
                                auto_grant=auto_grant)
    stopped = []
    for sim in to_stop:  # re-checked just before: an `ios up` since the reading keeps its simulator
        ios = _read(_lock_path(lock_dir, "ios"))
        if ios:
            release(lock_dir, "android", owner)
            raise DeviceQAError(f"측정 뒤 ios 잠금이 생겼습니다({ios['owner']}). {sim['name']}을 끄지 않았습니다 — "
                                "iOS·Android 동시 금지, 기다린 뒤 다시")
        try:
            run(["xcrun", "simctl", "shutdown", sim["udid"]])  # shutdown only, no erase: kept warm
        except DeviceQAError as error:  # no android lock without a serial is left behind
            release(lock_dir, "android", owner)
            raise DeviceQAError(f"QA 시뮬레이터를 끄지 못했습니다: {sim['name']} {sim['udid']} ({error}). "
                                f"끈 것: {', '.join(stopped) or '없음'}. android 잠금은 풀었습니다") from error
        stopped.append(f"{sim['name']} {sim['udid']}")
    if stopped:
        lock = update(lock_dir, "android", owner, stopped=stopped)
    emulators = _android_emulators(run([adb, "devices"]))
    not_ready = [(serial, state) for serial, state in emulators if state != "device"]
    if not_ready:  # starting beside one on our port would take it as ours, and `down` would stop it
        release(lock_dir, "android", owner)
        raise DeviceQAError(f"에뮬레이터 {not_ready[0][0]}이 {not_ready[0][1]}입니다(켜지는 중이거나 꺼지는 중) — 기다린 뒤 다시")
    running = [serial for serial, _ in emulators]
    if running:  # someone else's emulator: use it, never stop it, never change it
        refusal = _state_change_refusal("android", "running", False, running[0], **changes)
        if refusal:
            release(lock_dir, "android", owner)
            raise DeviceQAError(refusal)
        lock = update(lock_dir, "android", owner, serial=running[0], started=False, avd=None, reused=True)
        return _android_session(lock_dir, owner, root, lock, started_at, time.time(), log, url, appium_port, run, spec,
                                auto_grant=auto_grant)
    avds = run([str(root / "emulator/emulator"), "-list-avds"]).split()
    if avd not in avds:
        release(lock_dir, "android", owner)
        raise DeviceQAError(f"AVD {avd}이 없습니다(있는 것: {', '.join(avds) or '없음'}). QA AVD는 `setup --platform android --apply`, "
                            "사용자 AVD는 --avd <이름>")
    cmd = [str(root / "emulator/emulator"), "-avd", avd, "-gpu", "host", "-no-audio", "-no-snapshot-save",
           "-wipe-data" if wipe else "-read-only", "-port", str(port)] + ([] if window else ["-no-window"])
    serial = f"emulator-{port}"
    pid = spawn(cmd, log or Path(lock_dir) / "android-emulator.log")
    lock = update(lock_dir, "android", owner, serial=serial, started=True, avd=avd, emulator_pid=pid)
    if not wait_android_boot(run, adb, serial):
        raise DeviceQAError(f"{serial} 부팅이 끝나지 않았습니다. `android down` 후 다시")
    return _android_session(lock_dir, owner, root, lock, started_at, time.time(), log, url, appium_port, run, spec,
                            auto_grant=auto_grant)


def _android_session(lock_dir, owner, root, lock, started_at, booted_at, log, url, appium_port, run=run, spec=None,
                     auto_grant=True):
    java = _find_java(shutil.which, _FS(), os.environ)
    if not java:
        raise DeviceQAError("Java(17+)를 찾지 못했습니다 — `doctor --platform android`")
    spec = _installed_app(run, "android", lock["serial"], spec, root)
    env = {"ANDROID_HOME": str(root), "ANDROID_SDK_ROOT": str(root), "JAVA_HOME": str(Path(java).parents[1])}
    served = _appium_server(lock_dir, "android", owner, appium_port, log, env, (ANDROID_APPIUM_FLAG,))
    lock = _open_session(lock_dir, "android", owner, appium_port, lock["serial"], spec, auto_grant=auto_grant)
    done = time.time()
    shown = {"timings": {"boot_s": round(booted_at - started_at, 1), "server_s": round(served - booted_at, 1),
                         "session_s": round(done - served, 1), "total_s": round(done - started_at, 1)},
             "chrome_tabs": None if spec else _safari_tabs(lock)}
    if spec:
        shown.update(_enter_webview(lock))
    return {**lock, **shown, **android_open(lock_dir, owner, url)} if url else {**lock, **shown}


def android_open(lock_dir, owner, url, run=run):
    return ios_open(lock_dir, owner, url, run, device="android")


def android_eval(lock_dir, owner, expression=None, script=None, run=run):
    return ios_eval(lock_dir, owner, expression, script, run, device="android")


def android_tap(lock_dir, owner, selector, js=False, run=run):
    return ios_tap(lock_dir, owner, selector, js, run, device="android")


def android_type(lock_dir, owner, text, selector=None, clear=False, run=run):
    return ios_type(lock_dir, owner, text, selector, clear, run, device="android")


def android_dismiss(lock_dir, owner, run=run):
    return ios_dismiss(lock_dir, owner, run, device="android")


def android_context(lock_dir, owner, name=None):
    return ios_context(lock_dir, owner, name, device="android")


def android_push(lock_dir, owner, payload, run=run):
    raise DeviceQAError("Android push는 지원하지 않습니다(FCM 서버가 필요, 범위 밖). iOS 앱 모드에서만 `ios push`")


def android_record(lock_dir, owner, action, path=None, run=run, sdk=None):
    return _record(lock_dir, owner, action, path, "android", run, sdk)


def android_shot(lock_dir, owner, path, sdk=None):
    lock = own(lock_dir, "android", owner)
    cmd = [str(find_sdk(sdk) / "platform-tools/adb"), "-s", lock["serial"], "exec-out", "screencap", "-p"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeviceQAError(f"화면 캡처 실패: {error}")
    if proc.returncode != 0 or not proc.stdout:
        raise DeviceQAError(f"화면 캡처 실패(exit {proc.returncode}): {proc.stderr.decode(errors='replace').strip()[:300]}")
    Path(path).write_bytes(proc.stdout)


def _android_emulators(devices):
    """`adb devices` → [(serial, state)] for every emulator line, whatever its state (device, offline, unauthorized, …).
    `up` and `down` read the list with this one rule."""
    return [tuple(line.split("\t", 1)) for line in devices.splitlines() if line.startswith("emulator-") and "\t" in line]


def _android_listed(devices, serial):
    return any(listed == serial for listed, _ in _android_emulators(devices))


def _wait_android_gone(run, adb, serial, timeout):
    """`emu kill` returns before the emulator leaves `adb devices`; an `up` meanwhile would take the dying one for
    someone else's. Any line for the serial (device, offline, …) counts as still there."""
    end = time.time() + timeout
    while True:
        try:
            listed = run([adb, "devices"])
        except DeviceQAError as error:
            raise DeviceQAError(f"{serial}이 꺼졌는지 확인하지 못했습니다({error}). 잠금을 남깁니다 — 다시 `android down`")
        if not _android_listed(listed, serial):
            return
        if time.time() >= end:
            raise DeviceQAError(f"{serial}이 {timeout}초 안에 adb devices에서 사라지지 않았습니다. 잠금을 남깁니다 — "
                                "아직 꺼지는 중 — 다시 `android down`")
        time.sleep(1)


def android_down(lock_dir, owner, run=run, sdk=None, timeout=30):
    """Session → our Appium server → the emulator we started (never someone else's), waited until it is gone → lock."""
    lock = own(lock_dir, "android", owner)
    stopped = _down_recording(lock_dir, owner, "android", run, sdk)
    if lock.get("session_id") and lock.get("port"):
        try:
            webdriver("DELETE", _session(lock), timeout=120)
        except DeviceQAError:
            pass
    pid, port = lock.get("server_pid"), lock.get("port")
    if pid and _is_our_server(process_command(pid), port):
        _stop(pid, lambda command: _is_our_server(command, port), device="android")
    if lock.get("started"):
        adb = str(find_sdk(sdk) / "platform-tools/adb")
        try:
            run([adb, "-s", lock["serial"], "emu", "kill"])
        except DeviceQAError as error:  # a `down` again after a timeout: adb answers "not found" once it is gone
            try:
                gone = not _android_listed(run([adb, "devices"]), lock["serial"])
            except DeviceQAError:
                gone = False
            if not gone:
                raise DeviceQAError(f"{lock['serial']} 끄기 실패({error}). 잠금을 남깁니다 — 확인 뒤 다시 `android down`")
        else:
            _wait_android_gone(run, adb, lock["serial"], timeout)
    release(lock_dir, "android", owner)
    return stopped



# ---- CLI ---------------------------------------------------------------------------------------------------

# --- doctor / setup — check the toolchain without changing it; fix only with --apply ---


class _FS:
    """Real file system; tests pass a fake with the same two methods."""

    @staticmethod
    def exists(path):
        return Path(path).expanduser().exists()

    @staticmethod
    def glob(pattern):
        import glob as _glob
        return sorted(_glob.glob(str(Path(pattern).expanduser())))


def parse_version(text):
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return (int(match[1]), int(match[2]), int(match[3] or 0)) if match else None


def _version_text(version):
    return ".".join(map(str, version)) if version else None


def _query(run, argv):
    """stdout of a read-only query, or None when it cannot run."""
    try:
        return run(argv).strip()
    except DeviceQAError:
        return None


def _fix(commands=None, auto=True, note=None, stdin=None, env=None):
    return {"commands": commands or [], "auto": bool(auto and commands), "note": note, "stdin": stdin, "env": env or {}}


def _check(name, required, ok, found, want, fix=None):
    return {"name": name, "required": required, "ok": bool(ok), "found": found, "want": want, "fix": None if ok else fix}


def _find_appium_bin(which, run, fs, env):
    candidates = [env.get("APPIUM_BIN"), which("appium")]
    npm = which("npm")
    prefix = _query(run, [npm, "config", "get", "prefix"]) if npm else None
    if prefix:
        candidates.append(str(Path(prefix) / "bin" / "appium"))
    return next((c for c in candidates if c and fs.exists(c)), None)


def _find_java(which, fs, env):
    home = env.get("JAVA_HOME")
    candidates = [f"{home}/bin/java" if home else None, *(f"{c}/bin/java" for c in JAVA_CANDIDATES), which("java")]
    return next((c for c in candidates if c and fs.exists(c)), None)


def _find_sdk_root(fs, env, candidates=SDK_CANDIDATES):
    """A root that `setup` can finish: first one with sdkmanager (it can install the rest), else one that `find_sdk`
    would accept (emulator and adb). A root with adb alone (Android Studio without cmdline-tools) is not chosen,
    so a brew-installed cmdline-tools root is picked up on the next check."""
    roots = [str(Path(c).expanduser()) for c in (env.get("ANDROID_SDK_ROOT"), env.get("ANDROID_HOME"), *candidates) if c]
    with_tools = [r for r in roots if fs.exists(f"{r}/cmdline-tools/latest/bin/sdkmanager")]
    complete = [r for r in roots if fs.exists(f"{r}/emulator/emulator") and fs.exists(f"{r}/platform-tools/adb")]
    return (with_tools or complete or [None])[0]


def doctor(platform="all", lock_dir=None, run=run, fs=_FS(), which=shutil.which, env=None, load=None, sdk_candidates=SDK_CANDIDATES):
    """Read-only toolchain check. `ok` is about the required checks of `platform` only."""
    if platform not in ("ios", "android", "all"):
        raise DeviceQAError("--platform은 ios·android·all 가운데 하나")
    env = os.environ if env is None else env
    lock_dir = Path(lock_dir) if lock_dir else default_lock_dir()
    ios, android = platform in ("ios", "all"), platform in ("android", "all")
    checks = []
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
        probe = lock_dir / ".doctor"
        probe.write_text("")
        probe.unlink()
        checks.append(_check("lock_dir", True, True, str(lock_dir), "쓰기 가능"))
    except OSError as error:
        checks.append(_check("lock_dir", True, False, f"{lock_dir}: {error}", "쓰기 가능", _fix(auto=False, note="잠금 폴더를 만들 수 있는 경로로 $AGENT_OPS_DEVICE_LOCKS 또는 --lock-dir")))
    # iOS
    xcode = _query(run, ["xcodebuild", "-version"])
    checks.append(_check("xcode", ios, bool(xcode and xcode.startswith("Xcode")), (xcode or "").splitlines()[0] if xcode else None, "Xcode",
                         _fix(auto=False, note="App Store에서 Xcode 설치 후 `sudo xcode-select -s /Applications/Xcode.app` · `xcodebuild -runFirstLaunch`")))
    runtimes_raw = _query(run, ["xcrun", "simctl", "list", "runtimes", "-j"])
    runtimes = []
    try:
        runtimes = [r.get("name") for r in json.loads(runtimes_raw or "{}").get("runtimes", []) if r.get("platform") == "iOS" and r.get("isAvailable")]
    except (ValueError, AttributeError):
        pass
    checks.append(_check("ios_runtime", ios, bool(runtimes), runtimes, "iOS 런타임 1개 이상",
                         _fix([["xcodebuild", "-downloadPlatform", "iOS"]], auto=False, note="수 GB 내려받기 — 사람이 실행")))
    node = which("node")
    node_raw = _query(run, [node, "--version"]) if node else None
    node_version = parse_version(node_raw)
    node_ok = bool(node_version) and any(lo <= node_version and (hi is None or node_version < hi) for lo, hi in NODE_RANGES)
    checks.append(_check("node", ios or android, node_ok, node_raw if node_raw and not node_version else (_version_text(node_version) if node else None),
                         "20.19+ · 22.12+ · 24+", _fix([["brew", "install", "node"]], auto=False, note="node는 사용자가 관리(nvm·brew) — 버전만 맞추면 됨")))
    npm = which("npm")
    npm_raw = _query(run, [npm, "--version"]) if npm else None
    npm_version = parse_version(npm_raw)
    checks.append(_check("npm", ios or android, bool(npm_version) and npm_version >= (10, 0, 0), _version_text(npm_version) if npm_version else npm_raw, ">=10",
                         _fix([[npm or "npm", "install", "-g", "npm"]], auto=bool(npm))))
    appium = _find_appium_bin(which, run, fs, env)
    appium_raw = _query(run, [appium, "--version"]) if appium else None
    appium_version = parse_version(appium_raw)
    checks.append(_check("appium", ios or android, bool(appium_version) and appium_version >= (3, 0, 0), _version_text(appium_version) if appium_version else appium_raw, ">=3",
                         _fix([[npm or "npm", "install", "-g", "appium"]], note="설치 뒤 PATH에 없으면 APPIUM_BIN 또는 npm prefix/bin에서 찾음")))
    drivers = {}
    if appium:
        try:
            drivers = json.loads(_query(run, [appium, "driver", "list", "--installed", "--json"]) or "{}")
        except ValueError:
            drivers = {}
        if not isinstance(drivers, dict):  # a list or string answer is "no drivers", not a traceback
            drivers = {}
    for driver, needed in (("xcuitest", ios), ("uiautomator2", android)):
        found = "appium 없음" if not appium else (drivers.get(driver) or {}).get("version") or (driver if driver in drivers else None)
        checks.append(_check(f"driver_{driver}", needed, bool(appium and driver in drivers), found, "설치됨",
                             _fix([[appium or "<appium>", "driver", "install", driver]])))
    # Android
    java = _find_java(which, fs, env)
    java_raw = _query(run, [java, "--version"]) if java else None
    java_version = parse_version(java_raw)
    java_home = str(Path(java).parents[1]) if java else None
    java_fix = _fix([["brew", "install", "openjdk@21"]])
    if env.get("JAVA_HOME") and java and java.startswith(f"{env['JAVA_HOME']}/"):  # JAVA_HOME wins over anything we install
        java_fix = _fix(auto=False, note=f"JAVA_HOME={env['JAVA_HOME']}의 Java가 오래됨 — `unset JAVA_HOME`(도구가 openjdk@21을 찾음) 또는 JAVA_HOME을 17+로")
    checks.append(_check("java", android, bool(java_version) and java_version >= (17, 0, 0), _version_text(java_version) if java_version else java_raw, ">=17", java_fix))
    sdk_root = _find_sdk_root(fs, env, sdk_candidates)
    sdkmanager = f"{sdk_root}/cmdline-tools/latest/bin/sdkmanager" if sdk_root else None
    tools_ok = bool(sdkmanager and fs.exists(sdkmanager))
    checks.append(_check("android_cmdline_tools", android, tools_ok, sdkmanager if tools_ok else None, "sdkmanager",
                         _fix([["brew", "install", "--cask", "android-commandlinetools"]], note=f"설치 위치 {BREW_SDK}")))
    sdk_ok = bool(sdk_root and fs.exists(f"{sdk_root}/emulator/emulator") and fs.exists(f"{sdk_root}/platform-tools/adb"))
    sdk_env = {"JAVA_HOME": java_home} if java_home else {}
    checks.append(_check("android_sdk", android, sdk_ok, sdk_root if sdk_ok else None, "emulator·platform-tools",
                         _fix([[sdkmanager or "<sdkmanager>", "--licenses"], [sdkmanager or "<sdkmanager>", "--install", *SDK_PACKAGES]],
                              stdin="y\n" * 100, env=sdk_env, note="라이선스 동의(y)와 패키지 설치 — 수 GB")))
    images = fs.glob(f"{sdk_root}/system-images/*/*/arm64-v8a") if sdk_root else []
    image_names = [";".join(["system-images", *Path(i).parts[-3:]]) for i in images]
    checks.append(_check("android_system_image", android, bool(images), image_names, "arm64-v8a 시스템 이미지 1개 이상",
                         _fix([[sdkmanager or "<sdkmanager>", "--install", QA_AVD_IMAGE]], stdin="y\n" * 100, env=sdk_env)))
    avds = _query(run, [f"{sdk_root}/emulator/emulator", "-list-avds"]) if sdk_ok else None
    qa_avds = [a for a in (avds or "").splitlines() if a.startswith(QA_PREFIX)] if avds is not None else None
    avdmanager = f"{sdk_root}/cmdline-tools/latest/bin/avdmanager" if sdk_root else "<avdmanager>"
    image = QA_AVD_IMAGE if QA_AVD_IMAGE in image_names or not image_names else image_names[0]
    checks.append(_check("avd_qa", android, bool(qa_avds), qa_avds if avds is not None else None, f"{QA_PREFIX}* AVD 1개 이상(사용자 AVD는 쓰지 않음)",
                         _fix([[avdmanager, "create", "avd", "-n", QA_AVD, "-k", image, "-d", "pixel_7"]], stdin="no\n", env=sdk_env,
                              note="QA 전용 AVD만 만든다 — 기존 AVD는 건드리지 않음")))
    exports = [f"export ANDROID_HOME={sdk_root}" for _ in [0] if sdk_root and not (env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT"))]
    exports += [f"export JAVA_HOME={java_home}" for _ in [0] if java_home and not env.get("JAVA_HOME")]
    checks.append(_check("env", False, not exports, {k: env.get(k) for k in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME")}, "ANDROID_HOME·JAVA_HOME(uiautomator2 드라이버용, 도구는 없어도 찾음)",
                         _fix(auto=False, note="; ".join(exports) or None)))
    try:
        reading = load() if load else measure_load(lock_dir, run)
        over = reading.get("swap_gb", 0) > LIMITS["swap_gb"] or reading.get("load1", 0) > LIMITS["load1_per_core"] * max(reading.get("cores", 1), 1)
        checks.append(_check("load", False, not over, reading, "부하 기준(LIMITS)", _fix(auto=False, note="기다리거나 내 기기 끄기 — `status --load`")))
    except DeviceQAError as error:
        checks.append(_check("load", False, False, str(error), "측정 가능", _fix(auto=False, note="status --load로 다시")))
    return {"platform": platform, "ok": all(c["ok"] for c in checks if c["required"]), "checks": checks}


def run_step(argv, stdin=None, env=None):
    merged = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run([str(a) for a in argv], input=stdin, capture_output=True, text=True, timeout=3600, env=merged)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeviceQAError(f"실행 실패: {' '.join(map(str, argv))}: {error}")
    if proc.returncode != 0:
        raise DeviceQAError(f"실행 실패(exit {proc.returncode}): {' '.join(map(str, argv))}: {(proc.stderr or proc.stdout).strip()[-300:]}")
    return proc.stdout


def setup(platform="all", apply=False, lock_dir=None, run=run, fs=_FS(), runner=run_step, which=shutil.which, env=None, load=None,
          sdk_candidates=SDK_CANDIDATES):
    """Dry-run lists the fixes of failing required checks; --apply runs the automatic ones in doctor order, one check at a time, re-checking after each.
    Advice (env·load) is reported, never run."""
    kwargs = dict(lock_dir=lock_dir, run=run, fs=fs, which=which, env=env, load=load, sdk_candidates=sdk_candidates)
    report = doctor(platform, **kwargs)

    def plan(result, required=True):
        return [{"check": c["name"], "required": c["required"], "auto": c["fix"]["auto"], "commands": c["fix"]["commands"], "note": c["fix"]["note"]}
                for c in result["checks"] if not c["ok"] and c["fix"] and c["required"] == required]
    if not apply:
        return {"platform": platform, "applied": False, "plan": plan(report), "advice": plan(report, required=False), "doctor": report}
    applied, tried = [], set()
    for _ in range(len(report["checks"]) + 1):
        failing = next((c for c in report["checks"] if c["required"] and not c["ok"] and c["fix"] and c["fix"]["auto"] and c["name"] not in tried), None)
        if failing is None:
            break
        tried.add(failing["name"])
        done_so_far = f" (이미 적용: {', '.join(applied) or '없음'})"  # what was installed is not lost in the error
        for argv in failing["fix"]["commands"]:
            try:
                runner(argv, failing["fix"]["stdin"], failing["fix"]["env"])
            except DeviceQAError as error:
                raise DeviceQAError(f"{failing['name']} 설치 실패 — {error}{done_so_far}")
        report = doctor(platform, **kwargs)
        still = next((c for c in report["checks"] if c["name"] == failing["name"]), None)
        if still and not still["ok"]:
            raise DeviceQAError(f"{failing['name']} 설치 명령은 끝났지만 점검이 여전히 실패합니다: {still['found']} — 사람이 확인{done_so_far}")
        applied.append(failing["name"])
    manual = [p for p in plan(report) if not p["auto"]]
    return {"platform": platform, "applied": applied, "manual": manual, "advice": plan(report, required=False), "doctor": report}


def _script(path):
    if path is None:
        return None
    try:
        return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise DeviceQAError(f"스크립트 파일을 읽지 못했습니다: {path} ({error})")


def main(argv=None):
    parser = argparse.ArgumentParser(description="기기 QA: 기기 1대 1세션, QA 전용 iOS 시뮬레이터(Appium), Android 에뮬레이터")
    parser.add_argument("--lock-dir", default=None, help="기본 $AGENT_OPS_DEVICE_LOCKS 또는 ~/.agent-ops/device-locks")
    subs = parser.add_subparsers(dest="platform", required=True)
    status_parser = subs.add_parser("status", help="기기 잠금 목록")
    status_parser.add_argument("--load", action="store_true", help="잠금과 함께 부하 측정값·기준")
    for name, help_text in (("doctor", "도구 사슬 점검(상태 변경 없음, 필수 항목 실패면 exit 1)"), ("setup", "고칠 명령 계획; --apply로만 실행(QA-agentops- 기기만 만듦)")):
        sub = subs.add_parser(name, help=help_text)
        sub.add_argument("--platform", dest="target", choices=("ios", "android", "all"), default="all")
        if name == "setup":
            sub.add_argument("--apply", action="store_true", help="계획을 실제로 실행(기본은 dry-run)")
    ios = subs.add_parser("ios").add_subparsers(dest="action", required=True)
    android = subs.add_parser("android").add_subparsers(dest="action", required=True)
    specs = {
        (ios, "up"): [("--url", {}), ("--device-type", {"default": "iPhone 17"}), ("--port", {"type": int, "default": APPIUM_PORT}),
                      ("--app", {"help": "앱 모드: 시뮬레이터 빌드 .app 절대 경로"}), ("--bundle-id", {"help": "앱 모드: 설치된 앱"})],
        (ios, "open"): [("url", {})], (ios, "eval"): [("expression", {"nargs": "?"}),
                                                 ("--script", {"help": "여러 줄 스크립트 파일(- 는 표준 입력), 함수 본문으로 실행"})],
        (ios, "tap"): [("selector", {}), ("--js", {"action": "store_true", "help": "실제 터치 대신 스크립트 click"})],
        (ios, "type"): [("text", {}), ("--selector", {"help": "없으면 포커스된 요소"}),
                         ("--clear", {"action": "store_true", "help": "입력 전에 값 비우기"})],
        (ios, "shot"): [("path", {})], (ios, "dismiss"): [],
        (ios, "permission"): [("resource", {"choices": tuple(sorted(PERMISSION_LABELS))}),
                               ("action", {"choices": ("allow", "deny", "close", "inspect")})],
        (ios, "native"): [("step", {"choices": ("inspect",)})],
        (ios, "down"): [], (ios, "reset"): [("--device-type", {"default": "iPhone 17"})],
        (ios, "context"): [("name", {"nargs": "?", "help": "NATIVE_APP, WEBVIEW(첫 웹뷰) 또는 정확한 이름; 없으면 목록"})],
        (ios, "push"): [("payload", {"help": "알림 JSON 파일(앱 모드)"})],
        (ios, "record"): [("step", {"choices": ("start", "stop")}), ("--path", {"help": ".mp4 경로(기본 잠금 폴더)"})],
        (android, "up"): [("--url", {}), ("--avd", {"default": QA_AVD, "help": f"기본 {QA_AVD}(setup이 만든 QA AVD); 사용자 AVD는 이름으로"}),
                          ("--wipe", {"action": "store_true", "help": "QA AVD만: 데이터를 지우고 시작(-wipe-data)"}),
                          ("--no-auto-grant-permissions", {"action": "store_true", "help": "앱 권한 UX 검증: Appium 자동 권한 허용 끄기"}),
                          ("--window", {"action": "store_true"}), ("--port", {"type": int, "default": 5584}),
                          ("--appium-port", {"type": int, "default": APPIUM_PORT}), ("--sdk", {}),
                          ("--apk", {"help": "앱 모드: .apk 절대 경로"}), ("--package", {"help": "앱 모드: 설치된 앱(--activity와 함께)"}),
                          ("--activity", {})],
        (android, "open"): [("url", {})], (android, "eval"): [("expression", {"nargs": "?"}), ("--script", {})],
        (android, "tap"): [("selector", {}), ("--js", {"action": "store_true"})],
        (android, "type"): [("text", {}), ("--selector", {}), ("--clear", {"action": "store_true"})],
        (android, "dismiss"): [],
        (android, "permission"): [("resource", {"choices": tuple(sorted(PERMISSION_LABELS))}),
                                   ("action", {"choices": ("allow", "deny", "close", "inspect")})],
        (android, "native"): [("step", {"choices": ("inspect",)})],
        (android, "shot"): [("path", {}), ("--sdk", {})], (android, "down"): [("--sdk", {})],
        (android, "context"): [("name", {"nargs": "?"})], (android, "push"): [("payload", {})],
        (android, "record"): [("step", {"choices": ("start", "stop")}), ("--path", {}), ("--sdk", {})],
    }
    for (group, name), args in specs.items():
        sub = group.add_parser(name)
        sub.add_argument("--owner", required=True, help="세션 이름(잠금 주인)")
        for flag, opts in args:
            sub.add_argument(flag, **opts)
    args = parser.parse_args(argv)
    lock_dir = Path(args.lock_dir).expanduser() if args.lock_dir else default_lock_dir()
    try:
        if args.platform == "doctor":
            result = doctor(args.target, lock_dir=lock_dir)
        elif args.platform == "setup":
            result = setup(args.target, apply=args.apply, lock_dir=lock_dir)
        elif args.platform == "status":
            result = status(lock_dir)  # the plain list stays as it was
            if args.load:
                try:
                    load = measure_load(lock_dir)
                except DeviceQAError as error:  # the locks are still worth showing
                    load = {"error": str(error)}
                result = {"locks": result, "load": load, "limits": LIMITS}
        elif args.platform == "ios":
            o = args.owner
            result = {
                "up": lambda: ios_up(lock_dir, o, args.url, device_type=args.device_type, port=args.port, app=args.app,
                                     bundle_id=args.bundle_id),
                "open": lambda: ios_open(lock_dir, o, args.url),
                "eval": lambda: ios_eval(lock_dir, o, args.expression, script=_script(args.script)),
                "tap": lambda: ios_tap(lock_dir, o, args.selector, js=args.js),
                "type": lambda: ios_type(lock_dir, o, args.text, selector=args.selector, clear=args.clear),
                "dismiss": lambda: ios_dismiss(lock_dir, o),
                "permission": lambda: permission(lock_dir, o, args.resource, args.action),
                "native": lambda: native_inspect(lock_dir, o),
                "shot": lambda: ios_shot(lock_dir, o, args.path),
                "down": lambda: ios_down(lock_dir, o),
                "reset": lambda: ios_reset(lock_dir, o, device_type=args.device_type),
                "context": lambda: ios_context(lock_dir, o, args.name),
                "push": lambda: ios_push(lock_dir, o, args.payload),
                "record": lambda: ios_record(lock_dir, o, args.step, args.path),
            }[args.action]()
        else:
            o = args.owner
            result = {
                "up": lambda: android_up(lock_dir, o, sdk=args.sdk, avd=args.avd, window=args.window, port=args.port,
                                         url=args.url, appium_port=args.appium_port, wipe=args.wipe, apk=args.apk,
                                         package=args.package, activity=args.activity,
                                         auto_grant=not args.no_auto_grant_permissions),
                "open": lambda: android_open(lock_dir, o, args.url),
                "eval": lambda: android_eval(lock_dir, o, args.expression, script=_script(args.script)),
                "tap": lambda: android_tap(lock_dir, o, args.selector, js=args.js),
                "type": lambda: android_type(lock_dir, o, args.text, selector=args.selector, clear=args.clear),
                "dismiss": lambda: android_dismiss(lock_dir, o),
                "permission": lambda: permission(lock_dir, o, args.resource, args.action, device="android"),
                "native": lambda: native_inspect(lock_dir, o, device="android"),
                "shot": lambda: android_shot(lock_dir, o, args.path, sdk=args.sdk),
                "down": lambda: android_down(lock_dir, o, sdk=args.sdk),
                "context": lambda: android_context(lock_dir, o, args.name),
                "push": lambda: android_push(lock_dir, o, args.payload),
                "record": lambda: android_record(lock_dir, o, args.step, args.path, sdk=args.sdk),
            }[args.action]()
    except DeviceQAError as error:
        print(f"device_qa: {error}", file=sys.stderr)
        return 1
    if result is not None or getattr(args, "action", None) == "eval":
        print(json.dumps(result, ensure_ascii=False))  # `eval` prints null too, so "no value" is visible
    if args.platform == "doctor" and not result["ok"]:
        return 1  # required checks failed — the JSON above says which
    if args.platform == "setup" and args.apply and not result["doctor"]["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
