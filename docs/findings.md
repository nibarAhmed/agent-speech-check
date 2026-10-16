# Findings: how Windows and NVDA behave

These are the experiments behind code in `asc` that would otherwise look odd: what Windows Sandbox, Windows' focus rules and NVDA did, including what didn't work. The wire protocol itself is in [protocol.md](protocol.md). Unless a section says otherwise, the setup was portable NVDA 2026.2 in Windows Sandbox on Windows 11 Pro (build 26200).

## Windows Sandbox

**Inside the sandbox**
- It has **no Edge** and **no `notepad.exe`**. `mshta.exe` exists, and NVDA
  reads an `.html` file opened in it in browse mode.
- **Silence it** by running `net stop audiosrv /y` at logon: nothing is audible
  on the host. NVDA logs audio warnings but works, and still sends `wave`
  messages. Pressing the Volume Mute key from a script did not silence it.
- The `.wsb` `LogonCommand` works with mapped folders: read-only for inputs,
  writable for results. A relocatable Python (uv-managed CPython) runs from a
  read-only mapped folder. The Microsoft Store Python can't be used that way.
- Creating the portable copy inside takes about 13 s, with
  `nvda_<ver>.exe --create-portable-silent --portable-path=C:\nvda`.

**Starting and stopping it with `wsb`**
- `wsb start --config <xml>` starts a sandbox with no window and **no user
  session**: the LogonCommand doesn't run, and `wsb exec --run-as
  ExistingLogin` fails with "A specified logon session does not exist".
  `--run-as System` works.
- `wsb connect` logs the user in (the LogonCommand runs) and opens the window
  about 0.6 s later.
- If the connect client is closed, the session lives on, disconnected, and
  `exec` still works in it. But **NVDA goes silent there**: it connects and says
  "Connected as controlled computer", yet keys and focus changes produce no
  speech. So `asc` keeps the client running, minimised or hidden.
- Force-killing the sandbox client while its VM is still running makes the next
  launch attach to the session that is logging off ("The remote environment is
  logging off"). Wait until `vmmemWindowsSandbox` has exited before starting
  another sandbox.

**Network**
- With networking on, the sandbox sits on the Hyper-V Default Switch subnet
  (for example 172.24.82.2/20), and its gateway is the host's Default Switch
  address (172.24.80.1). NVDA inside reaches a relay bound to that address once
  one inbound firewall rule allows it (`asc sandbox-setup`). No firewall dialog
  appears with the rule in place.

**The sandbox window and focus**
- A visible sandbox window takes keyboard focus on the host: keys typed on the
  host (Alt+Tab) reached the sandbox and opened its task switcher in the middle
  of a test. The sandbox keeps working while minimised.
- Hidden (`ShowWindow` with `SW_HIDE`, no taskbar button) works as well as
  minimised.
- But hidden straight from the foreground, the client kept every key and click
  on the host until the sandbox closed. Minimising first, then hiding, avoids
  it.
- The client also keeps focus once its window is minimised or hidden (the host's
  NVDA says "Windows Sandbox pane"). So `asc` notes the focused window before
  `wsb connect` and gives focus back to it while an out-of-sight client window
  has it.
- **An app started through `wsb exec` doesn't get the foreground**, because of
  Windows' focus-stealing rules: `mshta` ran, but focus stayed on "Program
  Manager" and NVDA said nothing. `asc launch` therefore starts the app through
  a PowerShell script in the session, which sends the still mouse input described
  in the next section, then waits for the new window and brings it forward
  (`AttachThreadInput`, then `SetForegroundWindow`).


## Starting an app: the foreground lock

After recent input, Windows' foreground lock keeps a program started from a
background process (the daemon) behind the window in front. This is the normal
state on a real desktop. Observed in Windows Sandbox with the lock on, using
NVDA's IO log and a `SetWinEventHook` listener:

- The app (`mshta`) asks for the foreground. Windows refuses but still fires
  `EVENT_SYSTEM_FOREGROUND` for its window. NVDA finds Program Manager still in
  front, logs "Foreground took too long to change" and drops the event.
- Forcing the window forward afterwards (`AttachThreadInput`, then
  `SetForegroundWindow`) works, but Windows fires no second foreground event, so
  NVDA never learns of it. Same with a stock NVDA config. A switch forced this
  way fires no `EVENT_SYSTEM_FOREGROUND` at all, even to another window.
- These didn't help: `WaitForInputIdle` before focusing; `SW_RESTORE`,
  minimise and restore, or hide and show before focusing; starting with
  `SW_SHOWNOACTIVATE` (`mshta` activates anyway); `SwitchToThisWindow` (refused
  like the app's own attempt); `AllowSetForegroundWindow` from the daemon
  (error 5 while the lock is on, even when attached to the foreground thread).
- A real Alt+Tab after the refusal brought the window forward, but NVDA picked
  it up only at the next key: NVDA+T still said "Program Manager" in 2 of 3
  runs, and `--expect` heard nothing in 3 of 3. Not usable for recovery.
- What works: `SendInput` of one all-zero mouse input (`INPUT` type 0, which
  moves nothing; size 40 on x64, where 48 fails silently and returns 0), then
  `AllowSetForegroundWindow(ASFW_ANY)`. The app then comes forward by itself
  and NVDA says its title and focus. `mouse.nudge` does this before
  `asc launch` when the target may send input.
- To confirm the lock is on before trusting a run, check that
  `AllowSetForegroundWindow` fails with error 5.

## Installed and portable NVDA

- An installed NVDA's `nvda.exe` (`C:\Program Files\NVDA`) carries
  `uiAccess="True"` in its manifest and is byte-identical to
  `nvda_uiAccess.exe`; `nvda_noUIAccess.exe` sits next to it. A uiAccess exe
  normally has to be started through ShellExecute, not `CreateProcess`, so it
  wouldn't be `asc`'s child or sit in its kill-on-close job (not tried). And it
  can't run next to the user's NVDA anyway. So `asc` uses an installed NVDA only
  as it runs, through watch or shared mode.
- `nvda.exe`'s version resource holds the NVDA version (`FileVersion`
  `2026.2.0.57664`). `asc` reads it to refuse an NVDA older than 2026.2 (see
  the next section).
- A portable NVDA keeps its config in `<portable>\userConfig`. `asc` copies it
  and rewrites `nvda.ini` on top; see `nvda_config.py`.

## NVDA 2025.1

Remote Access is built in from 2025.1, but `asc` needs 2026.2.
`scripts/e2e_sandbox.py --nvda-version 2025.1` ran `tests/e2e/test_real_nvda.py`
in Windows Sandbox, with Windows Audio stopped as usual: every test failed,
where 2026.2 in the same setup passed them all.
- NVDA joined the relay, but asc never got any speech (`wait` timed out, not
  disconnected).
- NVDA's log: on joining, it plays a Remote Access cue; with no audio device,
  opening it raises `OSError: [WinError -2147023728] Element not found`
  (`nvwave.WavePlayer.open`). That exception ends `handleChannelJoined`
  early, and speech is never forwarded.
- Sandbox mode also stops Windows Audio, so with 2025.1 it should hit the
  same failure (not observed).

