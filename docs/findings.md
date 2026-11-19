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
- Its root certificate store starts nearly empty. Python's `ssl`, which reads
  only the roots already there, failed on api.github.com with
  `CERTIFICATE_VERIFY_FAILED`; PowerShell's `Invoke-WebRequest` worked, because
  Windows fetches a missing root when it checks a chain. So `asc` checks HTTPS
  through Windows (`truststore`).
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
  one inbound firewall rule allows it (`asc firewall-setup`). No firewall dialog
  appears with the rule in place.
- **The Default Switch does not last** (Windows 11 Pro, build 26300). About half
  an hour after the last sandbox closed, `vEthernet (Default Switch)` was gone.
  Starting a sandbox made it again with a new interface id (`3B811EAC...` became
  `DCBD0D9D...`) and, on other days, a new address.
- **A firewall rule on that interface dies with it.** A rule made with
  `-InterfaceAlias 'vEthernet (Default Switch)'` is stored with the id the
  interface had then (`IF={...}` in the registry), not the name. After the
  switch was made again the rule was still enabled but matched nothing: NVDA in
  the sandbox timed out connecting. So the rule names no interface. It is
  limited to `-RemoteAddress LocalSubnet` instead, which Windows works out from
  the subnets the computer is on at the time.

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
- A process's `MainWindowHandle` isn't always the window a user sees: `mshta`'s is a
  hidden "Internet Explorer_Hidden" window, which NVDA reads as "unknown". So the
  script leaves the focus alone when the app already has it, and otherwise picks
  the app's topmost visible window with a title.
- A batch file started with `start` opens a console of its own, which NVDA reads
  and which takes the focus. The script runs it with `call` in its own hidden
  console instead.


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
  `asc launch` when the mode may send input.
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


## Orca on Linux

Orca 51.1 on openSUSE Tumbleweed (AT-SPI 2.62.1), in a container with Xvfb and no audio. What
is said about watch mode was seen with Orca 51.0 on Ubuntu 26.10, in the same kind of container.

- **Orca 51 is the oldest that works.** Orca 50.3 (Ubuntu 26.04 LTS's package) publishes
  `org.gnome.Orca.Service`, not `org.gnome.Orca1.Service`, and has no
  `SetLogFileForTesting`, so it has nothing `asc` can record speech through.

- **No speech server is needed.** Orca fails to connect to Speech Dispatcher, logs it, and
  carries on. It still sends everything it would say to the recorder that
  `SpeechPresenter.SetLogFileForTesting` starts.
- **The recorder writes to a FIFO.** It opens the path for writing, one line per record, so a
  named pipe works. The reader must open the pipe first, because Orca's open blocks until there
  is a reader. A pipe nobody empties stalls Orca once about 64 KB wait in it, so the helper
  reads it on a thread of its own. A reader killed with SIGKILL does no harm: Orca carries on.
- **Orca's own key commands need a window manager.** With none, X input focus is `PointerRoot`
  (window 1). libatspi puts its key grabs on the focus window, so every grab fails with
  BadWindow (XInput opcode 131, minor 54), and Orca's debug log shows `Grab id for Insert: 0`.
  Plain keys still reach the app and Orca still speaks focus changes, but Insert+T types a "t".
  Setting the focus to the root window didn't help. With openbox running, the grabs work
  (`Grab id for Insert: 273`) and Insert+T speaks the time.
- **Xvfb resets when its last client disconnects**, which drops any focus or keymap a client
  set, so it runs with `-noreset`.
- **One Orca per user.** Orca refuses to start while another Orca runs as the same user
  (`pgrep -u <uid> -x orca`). `orca --replace` ends the other one, so only the user runs it.
- **The recording secret comes only from Orca's startup environment**
  (`ORCA_TEST_RPC_SECRET`). Orca zeroes its own `/proc/PID/environ`, so no one can read the
  secret off a running Orca. A wrong secret gets
  `GDBus.Error:not.known.Error.PermissionError: invalid testing token`, and an Orca started
  without one has no `SetLogFileForTesting` at all (UnknownMethod).
- **Restarting with the secret.** `ORCA_TEST_RPC_SECRET=... setsid orca --replace` starts a new
  Orca that takes the secret; the old one exits a moment later. `SetLogFileForTesting` with an
  empty path stops the recording and leaves Orca running.
- **Keys sent through `Atspi.generate_keyboard_event`** from a separate process produced no key
  events at all in this setup. Keys sent through XTest (xdotool) work.

## The ssh tunnel

Remote mode with `os = "windows"`: Windows' OpenSSH client (`C:\Windows\System32\OpenSSH`) and
Git's OpenSSH 10.3 on Windows 11, to sshd on Ubuntu in a container.

- **When the tunnel is up.** With `-v`, ssh prints
  `debug1: remote forward success for: listen 127.0.0.1:PORT, connect ...` once the other side
  listens. Before that line, nothing on the other machine can reach the relay yet.
- **A taken port.** With `ExitOnForwardFailure=yes`, ssh prints
  `Error: remote port forwarding failed for listen port PORT` and exits 255, the same code as a
  failed connection, so only the line tells the two apart.
- **Cost.** Against a fake NVDA in the container, 80 presses each (press to its speech, with a
  50 ms settle), and the old design's path (the fake straight to the relay across Docker's
  network) as the baseline:

  | Path | `asc start` | press median | press p95 |
  | --- | --- | --- | --- |
  | straight to the relay | 0.7 s | 76 ms | 104 ms |
  | tunnel, Windows' ssh | 0.8 s | 75 to 104 ms | 137 ms |
  | tunnel, Git's ssh | 0.8 s | 76 ms | 104 ms |

  Typing 10 characters took 263 ms on every path. Through Windows' ssh about half the replies
  came about 60 ms late, and with a 50 ms settle some presses ended before their speech
  arrived. The default 400 ms covers it. TCP_NODELAY on the relay's socket, or on the fake's,
  changed nothing; a tty session (`-tt` for `-N`) moved every press to about 105 ms. Windows'
  OpenSSH server wasn't measured.
