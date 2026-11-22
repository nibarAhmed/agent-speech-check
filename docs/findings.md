# Findings: how Windows, NVDA, Orca and ssh behave

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
- Hiding the window (`ShowWindow` with `SW_HIDE`, removing the taskbar button)
  works as well as minimising it.
- However, hiding the window directly while it is in the foreground causes the
  sandbox client to trap all host keyboard and mouse input until the sandbox
  closes. Minimising first and then hiding avoids this.
- Even after being minimised or hidden, the client retains focus (the host's
  NVDA announces "Windows Sandbox pane"). `asc` saves the previously focused
  window before `wsb connect` and restores focus to it whenever a hidden or
  minimised sandbox window holds focus.
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

After recent user input, Windows' foreground lock prevents a program started by
a background process (the daemon) from coming in front of the active window.
This is the normal state on a real desktop. Observed in Windows Sandbox with the
lock active, using NVDA's IO log and a `SetWinEventHook` listener:

- The app (`mshta`) requests the foreground. Windows denies the request but
  still fires `EVENT_SYSTEM_FOREGROUND` for its window. NVDA checks the
  foreground, sees Program Manager still in front, logs "Foreground took too
  long to change", and drops the event.
- Forcing the window forward afterwards (`AttachThreadInput`, then
  `SetForegroundWindow`) moves it visually to the front, but Windows fires no
  second `EVENT_SYSTEM_FOREGROUND` event (even when switching to a different
  window, and even with a stock NVDA config), so NVDA never learns of the focus
  change.
- None of these helped: `WaitForInputIdle` before focusing; `SW_RESTORE`,
  minimise/restore, or hide/show before focusing; launching with
  `SW_SHOWNOACTIVATE` (`mshta` activates anyway); `SwitchToThisWindow` (denied
  just like the app's own attempt); or calling `AllowSetForegroundWindow` from
  the daemon (fails with error 5 while the lock is active, even when attached
  to the foreground thread).
- Sending a real Alt+Tab after the refusal brought the window forward, but NVDA
  only noticed it on the next key: NVDA+T still announced "Program Manager" in
  2 of 3 runs, and `--expect` received nothing in 3 of 3 runs. Not reliable for
  recovery.
- What works: call `SendInput` with one all-zero mouse input (`INPUT` type 0,
  which moves nothing; must be 40 bytes on x64, because 48 bytes fails silently
  and returns 0), then call `AllowSetForegroundWindow(ASFW_ANY)`. The launched
  app then takes foreground on its own and NVDA announces its title and focus.
  `mouse.nudge` runs this before `asc launch` in modes allowed to send input.
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
Running `tests/e2e/test_real_nvda.py` against NVDA 2025.1 with Windows Audio
stopped as usual (reproducible via `gh workflow run e2e.yml -f nvda_version=2025.1`):
every test failed, where 2026.2 in the same setup passed them all.
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
- **The recorder writes to a FIFO.** Orca opens the path for writing and writes
  one JSON line per record, so a named pipe works. The reader must open the pipe
  first, because Orca's open blocks until a reader is attached. If nobody drains
  the pipe, Orca stalls once the ~64 KB pipe buffer fills, so the helper drains
  it continuously on a dedicated thread. Killing the reader with SIGKILL does no
  harm: Orca carries on.
- **Orca's own key commands need a window manager.** With none, X input focus is `PointerRoot`
  (window 1). libatspi puts its key grabs on the focus window, so every grab fails with
  BadWindow (XInput opcode 131, minor 54), and Orca's debug log shows `Grab id for Insert: 0`.
  Plain keys still reach the app and Orca still speaks focus changes, but Insert+T types a "t".
  Setting the focus to the root window didn't help. With openbox running, the grabs work
  (`Grab id for Insert: 273`) and Insert+T speaks the time.
- **Xvfb resets when its last client disconnects**, which drops any focus or keymap a client
  set, so it runs with `-noreset`.
- **One Orca per user.** Orca refuses to start if another `orca` process exists
  for the same UID (`pgrep -u <uid> -x orca`), including zombie processes (so
  containers need an init process like `docker run --init` to reap exited Orca
  processes). Because `orca --replace` kills the existing Orca, `asc` never runs
  it and leaves that to the user.
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

- **Windows' sshd.** Win32-OpenSSH 10.4's sshd (`scripts/windows_sshd.py`), run on the Windows CI
  runner, carries the tunnel as Ubuntu's does: a real NVDA joins through it. For an administrator it
  reads only `%ProgramData%\ssh\administrators_authorized_keys`, and only if no one but
  Administrators and SYSTEM can write that file. Windows Sandbox has no ssh client of its own.
- **When the tunnel is up.** With `-v`, ssh prints
  `debug1: remote forward success for: listen 127.0.0.1:PORT, connect ...` once the other side
  listens. Before that line, nothing on the other machine can reach the relay yet.
- **A taken port.** With `ExitOnForwardFailure=yes`, ssh prints
  `Error: remote port forwarding failed for listen port PORT` and exits 255, the same code as a
  failed connection, so only the line tells the two apart.
- **Cost.** Against a fake NVDA in the container, 80 presses each (press to its speech, with a
  50 ms settle), and the fake straight to the relay across Docker's network as a
  baseline:

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
