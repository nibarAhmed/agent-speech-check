"""M0 spike (throwaway): talk to a real NVDA over Remote Access, both routes.

Runs INSIDE Windows Sandbox (see docs/protocol.md), never on a desktop where
someone's own NVDA is running. `scripts/spike_run.cmd` is the sandbox's
LogonCommand: it stops Windows Audio, creates the portable NVDA and runs this
script. Expects:
  C:\\nvda\\nvda.exe                 portable NVDA
  C:\\spike\\in\\relay_cert.pem/.key  self-signed cert for route B, relay_fp.txt
  C:\\spike\\in\\test.html            a page for browse mode checks
Writes its log to stdout and a JSON summary to C:\\spike\\out\\summary.json.

Route B: this script is the relay. NVDA connects out to it as the follower.
Route A: NVDA hosts the relay (selfHosted). This script joins it as the leader.
Written from observed behaviour and docs, not from NVDA source.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import socket
import ssl
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

NVDA = Path(r"C:\nvda\nvda.exe")
IN = Path(r"C:\spike\in")
OUT = Path(r"C:\spike\out")
KEY = "spike-key-123"
B_PORT = 16837
EDGES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
MSHTA = Path(r"C:\Windows\System32\mshta.exe")
T0 = time.monotonic()
SUMMARY: dict[str, Any] = {"types": {}, "findings": {}}

user32 = ctypes.windll.user32


def log(*parts: object) -> None:
    print(f"[{time.monotonic() - T0:7.2f}]", *parts, flush=True)


def finding(name: str, value: object) -> None:
    SUMMARY["findings"][name] = value
    log("FINDING", name, "=", value)


# ---------------------------------------------------------------- connection


class Conn:
    def __init__(self, sock: ssl.SSLSocket, name: str) -> None:
        self.sock = sock
        self.name = name
        self.msgs: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        buf = b""
        try:
            while True:
                data = self.sock.recv(65536)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._handle(line)
        except OSError as e:
            log(self.name, "read error", repr(e))
        self.alive = False
        log(self.name, "closed")

    def _handle(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            log(self.name, "BAD LINE", line[:300])
            return
        with self.lock:
            self.msgs.append(msg)
        t = str(msg.get("type"))
        examples = SUMMARY["types"].setdefault(t, [])
        if len(examples) < 3:
            examples.append(msg)
        log(self.name, "<-", json.dumps(msg)[:500])

    def send(self, msg: dict[str, Any]) -> None:
        self.sock.sendall(json.dumps(msg).encode() + b"\n")
        log(self.name, "->", json.dumps(msg))

    def mark(self) -> int:
        with self.lock:
            return len(self.msgs)

    def since(self, n: int) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.msgs[n:])

    def wait_type(self, t: str, timeout: float, start: int = 0) -> dict[str, Any] | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for m in self.since(start):
                if m.get("type") == t:
                    return m
            time.sleep(0.05)
        return None


def spoken(msgs: list[dict[str, Any]]) -> list[str]:
    out = []
    for m in msgs:
        if m.get("type") == "speak":
            text = " ".join(i for i in m.get("sequence", []) if isinstance(i, str))
            out.append(text)
        elif m.get("type") in {"wave", "tone", "cancel"}:
            out.append(f"<{m['type']} {json.dumps({k: v for k, v in m.items() if k != 'type'})}>")
    return out


# ---------------------------------------------------------------- keys

VK: dict[str, tuple[int, bool]] = {
    "nvda": (0x2D, True),
    "tab": (0x09, False),
    "up": (0x26, True),
    "escape": (0x1B, False),
    "shift": (0x10, False),
    "space": (0x20, False),
    "t": (0x54, False),
    "h": (0x48, False),
    "i": (0x49, False),
}


def press(conn: Conn, gesture: str, settle: float = 1.5) -> list[str]:
    keys = [VK[k] for k in gesture.lower().split("+")]
    n = conn.mark()
    for vk, ext in keys:
        conn.send({"type": "key", "vk_code": vk, "extended": ext, "pressed": True})
    for vk, ext in reversed(keys):
        conn.send({"type": "key", "vk_code": vk, "extended": ext, "pressed": False})
    time.sleep(settle)
    said = spoken(conn.since(n))
    log("PRESS", gesture, "=>", said)
    SUMMARY.setdefault("presses", []).append({"gesture": gesture, "said": said})
    return said


def shift_held() -> bool:
    return bool(user32.GetAsyncKeyState(0x10) & 0x8000)


# ---------------------------------------------------------------- windows / processes


def window_titles() -> list[str]:
    titles: list[str] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value:
                titles.append(buf.value)
        return True

    user32.EnumWindows(proc(cb), 0)
    return titles


def netstat(port: int) -> list[str]:
    out = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"], check=False, capture_output=True, text=True
    ).stdout
    out6 = subprocess.run(
        ["netstat", "-ano", "-p", "tcpv6"], check=False, capture_output=True, text=True
    ).stdout
    return [ln.strip() for ln in (out + out6).splitlines() if f":{port} " in ln]


def write_ini(cfg: Path, remote: str) -> None:
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "nvda.ini").write_text(
        "[general]\n"
        "showWelcomeDialogAtStartup = False\n"
        "[update]\n"
        "autoCheckForUpdates = False\n"
        "askedAllowUsageStats = True\n"
        "allowUsageStats = False\n"
        "[speech]\n"
        "synth = silence\n"
        "[keyboard]\n"
        "keyboardLayout = desktop\n"
        "[virtualBuffers]\n"
        "passThroughAudioIndication = True\n" + remote,
        encoding="utf-8",
    )


def start_nvda(cfg: Path, tag: str) -> subprocess.Popen[bytes]:
    log("starting NVDA", tag)
    return subprocess.Popen(
        [str(NVDA), f"--config-path={cfg}", f"--log-file={OUT / f'nvda_{tag}.log'}", "-l", "10"]
    )


def stop_nvda(proc: subprocess.Popen[bytes]) -> None:
    subprocess.run([str(NVDA), "-q"], check=False, timeout=30)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    for _ in range(40):
        r = subprocess.run(["tasklist"], check=False, capture_output=True, text=True).stdout.lower()
        if "nvda.exe" not in r:
            break
        time.sleep(0.5)
    log("NVDA stopped")


def common_checks(conn: Conn, tag: str) -> None:
    time.sleep(3)
    log(f"[{tag}] startup speech:", spoken(conn.since(0)))
    press(conn, "nvda+t")
    press(conn, "nvda+tab")


# ---------------------------------------------------------------- route B


class Relay:
    """Accepts every connection; the newest one that sends `join` is the follower."""

    def __init__(self) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(IN / "relay_cert.pem", IN / "relay_key.pem")
        self.ctx = ctx
        self.srv = socket.create_server(("127.0.0.1", B_PORT))
        self.conns: list[Conn] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                raw, addr = self.srv.accept()
            except OSError:
                return
            log("B accept", addr)
            try:
                tls = self.ctx.wrap_socket(raw, server_side=True)
            except (ssl.SSLError, OSError) as e:
                log("B TLS handshake failed (expected on NVDA's first verify attempt):", repr(e))
                continue
            self.conns.append(Conn(tls, f"B{len(self.conns)}"))

    def follower(self, timeout: float) -> Conn | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for c in reversed(self.conns):
                join = c.wait_type("join", 0.05)
                if join and c.alive and not getattr(c, "joined", False):
                    finding("B_follower_join", join)
                    c.send(
                        {
                            "type": "channel_joined",
                            "channel": KEY,
                            "user_ids": [1],
                            "clients": [{"id": 1, "connection_type": "master"}],
                        }
                    )
                    c.joined = True  # type: ignore[attr-defined]
                    return c
            time.sleep(0.2)
        return None


def route_b() -> None:
    fp = (IN / "relay_fp.txt").read_text().strip()
    cfg = Path(r"C:\cfgB")
    write_ini(
        cfg,
        "[remote]\n"
        "enabled = True\n"
        "[[controlServer]]\n"
        "autoconnect = True\n"
        "selfHosted = False\n"
        "connectionMode = 0\n"
        f"host = 127.0.0.1:{B_PORT}\n"
        f"port = {B_PORT}\n"
        f"key = {KEY}\n"
        "[[trustedCertificates]]\n"
        f'"127.0.0.1:{B_PORT}" = {fp}\n',
    )
    relay = Relay()
    proc = start_nvda(cfg, "B")
    try:
        conn = relay.follower(90)
        finding("B_connected", conn is not None)
        log("B windows:", window_titles())
        finding("B_nvda_listening_ports", netstat(6837))
        if conn is None:
            return
        common_checks(conn, "B")
        browse_checks(conn)
        key_release_checks(relay, conn)
    finally:
        stop_nvda(proc)
        relay.srv.close()


def browse_checks(conn: Conn) -> None:
    url = (IN / "test.html").as_uri()
    edge_path = next((e for e in EDGES if e.exists()), None)
    finding("edge_path", str(edge_path))
    finding("mshta_exists", MSHTA.exists())
    if edge_path is not None:
        cmd = [str(edge_path), "--no-first-run", "--inprivate", "--start-maximized", url]
    elif MSHTA.exists():
        cmd = [str(MSHTA), str(IN / "test.html")]
    else:
        return
    edge = subprocess.Popen(cmd)
    time.sleep(12)
    n = conn.mark()
    time.sleep(0.5)
    log("[B] after edge launch:", spoken(conn.since(n)), window_titles())
    press(conn, "h")
    press(conn, "h")
    press(conn, "tab")  # into the edit field: focus mode
    press(conn, "h", 0.8)
    press(conn, "i", 0.8)
    press(conn, "nvda+up")  # current line: should read "hi"
    press(conn, "escape")  # back to browse mode
    press(conn, "nvda+space")
    press(conn, "nvda+space")
    edge.kill()
    for image in ("msedge.exe", "mshta.exe"):
        subprocess.run(["taskkill", "/f", "/im", image], capture_output=True, check=False)
    time.sleep(2)


def key_release_checks(relay: Relay, conn: Conn) -> None:
    master = {"id": 1, "connection_type": "master"}
    # 1. leader leaves the channel while Shift is down
    conn.send({"type": "key", "vk_code": 0x10, "extended": False, "pressed": True})
    time.sleep(0.5)
    finding("shift_held_after_down", shift_held())
    conn.send({"type": "client_left", "user_id": 1, "client": master, "origin": 1})
    time.sleep(1.5)
    finding("shift_held_after_client_left", shift_held())
    conn.send({"type": "client_joined", "user_id": 1, "client": master, "origin": 1})
    time.sleep(0.5)
    conn.send({"type": "key", "vk_code": 0x10, "extended": False, "pressed": False})
    time.sleep(0.5)
    finding("shift_held_after_release", shift_held())
    # 2. the TLS connection drops while Shift is down
    conn.send({"type": "key", "vk_code": 0x10, "extended": False, "pressed": True})
    time.sleep(0.5)
    conn.sock.close()
    time.sleep(2)
    finding("shift_held_after_socket_drop", shift_held())
    again = relay.follower(30)
    finding("B_reconnected_after_drop", again is not None)
    if again is not None:
        time.sleep(1)
        again.send({"type": "key", "vk_code": 0x10, "extended": False, "pressed": False})
        time.sleep(0.5)
        finding("shift_held_after_reconnect_release", shift_held())
        press(again, "nvda+t")


# ---------------------------------------------------------------- route A


def leader_connect(fp_expected: str | None) -> tuple[Conn, str]:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection(("127.0.0.1", 6837), timeout=5)
    tls = ctx.wrap_socket(raw)
    tls.settimeout(None)
    der = tls.getpeercert(binary_form=True) or b""
    fp = hashlib.sha256(der).hexdigest()
    if fp_expected is not None:
        finding("A_fingerprint_matches_file", fp == fp_expected)
    return Conn(tls, "A"), fp


def route_a() -> None:
    cfg = Path(r"C:\cfgA")
    write_ini(
        cfg,
        "[remote]\n"
        "enabled = True\n"
        "[[controlServer]]\n"
        "autoconnect = True\n"
        "selfHosted = True\n"
        "connectionMode = 0\n"
        "port = 6837\n"
        f"key = {KEY}\n",
    )
    proc = start_nvda(cfg, "A")
    try:
        conn = None
        for _ in range(90):
            try:
                fp_file = cfg / "remoteAccess" / "localRelay" / "NvdaRemoteRelay.fingerprint"
                expected = fp_file.read_text().strip() if fp_file.exists() else None
                conn, _fp = leader_connect(expected)
                break
            except OSError:
                time.sleep(1)
        finding("A_connected_tcp", conn is not None)
        finding("A_listening", netstat(6837))
        finding("A_windows_after_listen", window_titles())
        finding(
            "A_relay_files",
            sorted(p.name for p in (cfg / "remoteAccess" / "localRelay").glob("*"))
            if (cfg / "remoteAccess" / "localRelay").exists()
            else [],
        )
        if conn is None:
            return
        conn.send({"type": "protocol_version", "version": 2})
        conn.send({"type": "join", "channel": KEY, "connection_type": "master"})
        finding("A_channel_joined", conn.wait_type("channel_joined", 10))
        common_checks(conn, "A")
        # wrong key: error shape
        bad, _ = leader_connect(None)
        bad.name = "Abad"
        bad.send({"type": "protocol_version", "version": 2})
        bad.send({"type": "join", "channel": "wrong", "connection_type": "master"})
        finding("A_wrong_key_reply", bad.wait_type("error", 5))
        time.sleep(1)
        finding("A_wrong_key_conn_closed", not bad.alive)
        conn.sock.close()
    finally:
        stop_nvda(proc)


def watchdog() -> None:
    time.sleep(900)
    log("WATCHDOG: giving up")
    dump()
    os._exit(3)


def dump() -> None:
    (OUT / "summary.json").write_text(json.dumps(SUMMARY, indent=2), encoding="utf-8")


def main() -> None:
    threading.Thread(target=watchdog, daemon=True).start()
    log("windows at start:", window_titles())
    for name, fn in (("B", route_b), ("A", route_a)):
        log(f"======== route {name}")
        try:
            fn()
        except Exception as e:
            log(f"route {name} crashed:", repr(e))
            finding(f"{name}_crash", repr(e))
        dump()
    log("spike finished")


if __name__ == "__main__":
    main()
