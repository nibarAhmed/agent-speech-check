"""Remote mode's other machine. One instance at a time holds it, and `asc type` maps
characters to keys with its keyboard layout, not this computer's.

The machine is known by its ssh host key, so an alias and an address for it match.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from agent_speech_check.errors import FileUnusable, OperationalError
from agent_speech_check.inputs.keys import Layout
from agent_speech_check.system import locks, paths, powershell, ssh
from agent_speech_check.validate import Fields

_HOST_KEY = "debug1: Server host key: "
# What `asc type` can type there: printable ASCII and Latin-1.
CHARS = "".join(map(chr, (*range(0x20, 0x7F), *range(0xA1, 0x100))))
_STANDARD = re.compile(r"0000([0-9a-fA-F]{4})")
# VkKeyScanW's shift state bits that `keys` turns into Shift, Control and Alt.
_SHIFT_STATES = 0x7
# VkKeyScanW's answers: -1 for none, else a key code and a shift state.
_SCANS = range(-1, 0x10000)
# Over a pipe PowerShell writes errors as CLIXML: the trap writes one plain line.
_LAYOUT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
trap { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
Add-Type -Namespace Asc -Name Keys -MemberDefinition @'
[DllImport("user32.dll")] public static extern short VkKeyScanW(char c);
[DllImport("user32.dll")] public static extern System.IntPtr GetKeyboardLayout(uint thread);
'@
$layouts = 'HKCU:\Keyboard Layout'
$preload = (Get-ItemProperty -LiteralPath "$layouts\Preload" -ErrorAction Ignore).'1'
# Substitutes names the layout Preload's language types with, like US-International for en-US.
$substitutes = Get-ItemProperty -LiteralPath "$layouts\Substitutes" -ErrorAction Ignore
if ($preload -and $substitutes.$preload) { $preload = $substitutes.$preload }
$scans = foreach ($c in $chars.ToCharArray()) { [int][Asc.Keys]::VkKeyScanW($c) }
$hkl = [Asc.Keys]::GetKeyboardLayout(0).ToInt64()
ConvertTo-Json -Compress -InputObject @{ hkl = $hkl; preload = $preload; scans = @($scans) }
"""


@dataclass(frozen=True)
class Machine:
    lock: locks.Lock
    # None: unknown, and `unknown` says why, so `asc type` refuses rather than type wrong text.
    layout: Layout | None
    unknown: str = ""


async def claim(destination: str, owner: str, *, windows: bool) -> Machine:
    """Hold the machine `destination` reaches for the instance `owner`, or raise if another
    instance holds it. On Windows, also read its keyboard layout."""
    remote = powershell.command_line(f"$chars = {powershell.quote(CHARS)}{_LAYOUT_SCRIPT}")
    code, stdout, lines = await ssh.run_fresh(destination, remote if windows else "exit")
    lock = hold(host_key(destination, lines), owner, destination)
    if not windows:
        return Machine(lock, None)
    if code != 0:
        return Machine(lock, None, ssh.last_said(ssh.said(lines), code))
    try:
        return Machine(lock, layout(stdout))
    except OperationalError as error:
        return Machine(lock, None, error.message)


def host_key(destination: str, lines: Sequence[str]) -> str:
    """The other machine's host key, from ssh -v. The last one: with ProxyJump the jump
    host's comes first."""
    keys = [line.removeprefix(_HOST_KEY) for line in lines if line.startswith(_HOST_KEY)]
    if not keys:
        raise OperationalError(
            f"ssh {destination} showed no host key, so asc can't tell the machine"
        )
    return keys[-1].strip()


def hold(key: str, owner: str, destination: str) -> locks.Lock:
    """Hold the machine with host key `key` until this process exits."""
    name = hashlib.sha256(key.encode()).hexdigest()[:32]
    folder = paths.base_root() / "machines"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        lock = locks.take(folder / f"{name}.lock")
    except OSError as error:
        raise FileUnusable.from_os(folder, error) from None
    owner_file = folder / f"{name}.owner"
    if lock is None:
        try:
            other = owner_file.read_text(encoding="utf-8").strip()[:100]
        except OSError:
            other = ""
        raise OperationalError(
            f"{destination} is in use by {f'instance {other}' if other else 'another instance'}",
            hint="wait until it stops, or ask the user",
        )
    paths.write_atomic(owner_file, owner)
    return lock


def layout(stdout: bytes) -> Layout:
    """The layout from `_LAYOUT_SCRIPT`'s answer. Raises if it may not be the one NVDA's
    desktop types with: ssh sessions there can use another."""
    try:
        raw = json.loads(stdout)
    except ValueError:
        raise OperationalError("PowerShell's answer is not the keyboard layout") from None
    f = Fields(raw, "keyboard layout", OperationalError, ("hkl", "preload", "scans"))
    hkl = f.number("hkl", low=-(2**63), high=2**64 - 1) & 0xFFFFFFFF
    preload = f.opt_text("preload", max_len=8)
    scans = f.items("scans")
    if len(scans) != len(CHARS) or not all(type(scan) is int and scan in _SCANS for scan in scans):
        raise OperationalError(f"keyboard layout scans: expected {len(CHARS)} key codes")
    if preload is None:
        raise OperationalError("there is no default keyboard layout")
    standard = _STANDARD.fullmatch(preload)
    lang = int(standard.group(1), 16) if standard else -1
    if hkl != (lang << 16 | lang):
        raise OperationalError(f"ssh sessions there use {hkl:08x}, not {preload}")
    found: dict[str, tuple[int, int]] = {}
    for char, scan in zip(CHARS, scans, strict=True):
        state = scan >> 8
        if scan != -1 and not state & ~_SHIFT_STATES:
            found[char] = (scan & 0xFF, state)
    return found
