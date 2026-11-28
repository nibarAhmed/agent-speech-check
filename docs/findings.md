# Findings: how Windows, NVDA, Orca and ssh behave

These are the experiments behind code in `asc` that would otherwise look odd: what Windows' focus rules, NVDA, Orca and ssh did, including what didn't work. The wire protocol itself is in [protocol.md](protocol.md).

## Starting an app: the foreground lock

After recent user input, Windows' foreground lock prevents a program started by
a background process (the daemon) from coming in front of the active window.
This is the normal state on a real desktop. Observed on Windows 11 with portable
NVDA 2026.2 and the lock active, using NVDA's IO log and a `SetWinEventHook`
listener:

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
  processes). Because `orca --replace` kills the existing Orca, desktop mode
  never runs it, and watch mode only runs it in a desktop session when the user
  agrees (or sets `restart_orca = "always"`).
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
  Administrators and SYSTEM can write that file.
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

## The other machine in remote mode

A Windows 11 computer with a Norwegian layout, to an en-US Windows 11 VM over Windows' OpenSSH.

- **Typing with this computer's layout.** `asc type` once mapped characters with this computer's
  `VkKeyScanW` and sent those keys to the VM, so `C:\Users\` was heard as "C greater Users" and
  `Ctrl+Alt+Shift` as "Ctrl equals Alt equals Shift".
- **Reading the VM's layout.** In an ssh session there, `LoadKeyboardLayoutW` returned 0 for every
  layout tried (00000409, 00000414, 0000041d, flags 0, 1 and 0x80), so no other layout can be
  loaded to map with. `GetKeyboardLayout(0)` gave 04090409, the same as the user's
  `HKCU\Keyboard Layout\Preload\1` (00000409), and `VkKeyScanW` mapped with it. Whether ssh
  sessions follow `Preload\1` for a user with another layout wasn't tested, so `asc type` maps
  only when the two agree. The PowerShell call, `Add-Type` included, took 1.2 s.
- **Knowing the machine.** `ssh -v` prints `debug1: Server host key: TYPE SHA256:...` on each new
  connection (0.3 s), the jump host's first with ProxyJump, and none over a shared
  (ControlMaster) connection. The Windows MachineGuid would need PowerShell, which the Linux sshd
  that stands in for Windows in the e2e tunnel tests doesn't have.
- **Two instances on one VM.** Both used port 26837 there and both said `tunnel up`, but NVDA
  reached only the first. The cause wasn't reproduced.
