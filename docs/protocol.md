# NVDA Remote Access protocol, as `asc` uses it

There are no official docs for this protocol. This file records what we
**observed** against a real NVDA. Where something comes only from reading NVDA's
source (facts only, no code copied) and was not seen on the wire, it says "not
observed".
`asc` implements from this file. It never copies or imports NVDA code (GPL).

Observed with: portable NVDA 2026.2 inside Windows Sandbox, M0 spike
(removed; read it with `git show bbe3f16:scripts/spike_remote.py`), 2026-10-11.
Three runs; the results below are from runs 2 and 3.

## Decision: route B

`asc` hosts the relay and NVDA connects **out** to it as the follower.

Route A (NVDA hosts the relay with `selfHosted = True`) is rejected:
- NVDA's relay listens on **all interfaces**: `0.0.0.0:6837` and `[::]:6837`.
  Anyone on the network who knows the key could drive it.
- Its first listen raised a **Windows Firewall dialog** ("Do you want to allow
  public and private networks to access this app? ... NVDA application (no UI
  Access)"). NVDA read the dialog aloud, and it took focus away from the app
  under test. Unattended runs can't get past it.

Under route B, NVDA opened no listening port at all (`netstat` showed nothing on
6837), and no dialog appeared.

## Transport

- TLS, then UTF-8 JSON objects, one per line, terminated by `\n`.
- Key order inside objects varies; parse by name.
- Default port 6837. Any port works if `host` names it.

## Handshake (follower NVDA to our relay)

What NVDA does on startup with the route B config below:

1. Connects and verifies the certificate normally. Our self-signed cert fails
   that (`TLSV1_ALERT_UNKNOWN_CA` on our side), so NVDA drops the connection.
2. Opens a second connection only to fetch the certificate, then closes it.
3. Finds the cert's fingerprint in `trustedCertificates`, then connects a third
   time, pinned to that fingerprint, and sends:
   ```json
   {"type": "protocol_version", "version": 2}
   {"type": "join", "channel": "<key>", "connection_type": "slave"}
   ```
4. We reply with `channel_joined` and list a leader, otherwise NVDA sends no
   speech:
   ```json
   {"type": "channel_joined", "channel": "<key>", "user_ids": [1],
    "clients": [{"id": 1, "connection_type": "master"}]}
   ```
   Each `clients` entry needs both `id` and `connection_type`.
5. NVDA speaks "Connected as controlled computer" (priority 2) and plays the
   `controlled.wav` and `controlling.wav` cues.

The whole thing took about 2 s from NVDA's start. So the relay must accept
connections that fail the TLS handshake or close straight away, and keep
listening.

**Reconnect:** when the TCP connection dropped, NVDA reconnected about 5 s later
and repeated steps 3 to 5.

**Leaving and rejoining without disconnecting:** we can tell NVDA that the leader
left or rejoined over the same connection:
```json
{"type": "client_left",   "user_id": 1, "client": {"id": 1, "connection_type": "master"}, "origin": 1}
{"type": "client_joined", "user_id": 1, "client": {"id": 1, "connection_type": "master"}, "origin": 1}
```
NVDA plays `disconnected.wav` on `client_left`.

## Messages from NVDA (follower to leader)

All observed unless marked otherwise.

| type | shape | notes |
|---|---|---|
| `speak` | `{"type":"speak","sequence":[...],"priority":0}` | `sequence` mixes strings (the spoken text) with command items such as `["LangChangeCommand",{...}]`, `["CharacterModeCommand",{...}]` and `["EndUtteranceCommand",{}]`. Keep the strings and ignore the rest. Strings carry trailing spaces, so strip them. `priority` is an int (0 normal, 2 seen for "Connected as controlled computer"). |
| `cancel` | `{"type":"cancel"}` | Sent before nearly every new utterance caused by a key. |
| `wave` | `{"type":"wave","fileName":"C:\\nvda\\waves\\focusMode.wav","asynchronous":true,"isSpeechWaveFileCommand":false}` | `fileName` is a full path on NVDA's machine. Use only the base name. |
| `pause_speech` | `{"type":"pause_speech","switch":true}` | Sent when Shift goes down (NVDA pauses speech), and with `false` when it's released. |
| `tone` | `{"type":"tone","hz":...,"length":...,"left":...,"right":...}` | Not observed. |
| `display` | `{"type":"display","cells":[...]}` | Braille. Sent only after a leader reports a braille display, which `asc` never does. Not observed. |

When `asc` connects as a leader to NVDA's own relay (route A, and watch mode if
the user runs the relay themselves), every forwarded message also carries
`"origin": <sender id>`.

### Mode switches (browse and focus)

With `[virtualBuffers] passThroughAudioIndication = True` (NVDA's default):
- **Into focus mode:** a `wave` with `focusMode.wav`. Seen on Tab into an edit
  field, and on NVDA+Space.
- **Into browse mode:** a `wave` with `browseMode.wav`. Seen on Escape out of the
  field, and on NVDA+Space.
- **Page load:** no wave. Loading a page in browse mode sent no sound, which
  confirms the plan: the mode is `unknown` until the first switch.

When that setting is off, NVDA speaks "focus mode" or "browse mode" instead (not
observed).

### Examples (exact text, trailing spaces stripped)

| Gesture | Where | Strings in `speak` |
|---|---|---|
| NVDA+T | desktop | `Program Manager` |
| NVDA+Tab | desktop | `Recycle Bin`, `list item`, `focused`, `1 of 2` |
| H | test page, browse mode | `Second heading`, `heading`, `level 2` |
| H again | last heading passed | `no next heading` |
| Tab | into the edit field | `Your name`, `edit`, `blank`, then a `focusMode.wav` wave |
| H, I | typing in the field | `h`, then `i` (character mode commands around each) |
| NVDA+Up | in the field | `hi` |

## Messages to NVDA (leader to follower)

```json
{"type": "key", "vk_code": 45, "extended": true, "pressed": true}
```
- Press the modifiers in order, then the key. Release in reverse order.
- The NVDA key is Insert: `vk_code` 0x2D (45), `extended: true`. Arrow keys are
  extended too.
- NVDA injects these keys exactly like real typing. Everything in the table
  above came from keys sent this way.

### Held keys are NOT released for us (observed)

- After Shift down, a `client_left` for the leader left Shift **held**.
- After Shift down, dropping the TCP connection left Shift **held**. It stayed
  held after NVDA reconnected, until we sent `pressed: false`.

So `asc` must release keys itself, which the plan's guaranteed-release design
already does. After a reconnect, the daemon should first send a release for
every key it may have left down.

## NVDA config for the follower (`nvda.ini`)

Route B, as used in the spike (only for an NVDA that `asc` launches):
```ini
[remote]
enabled = True
[[controlServer]]
autoconnect = True
selfHosted = False
connectionMode = 0
host = 127.0.0.1:16837
port = 16837
key = <random key>
[[trustedCertificates]]
"127.0.0.1:16837" = <lowercase hex SHA-256 of the relay cert, DER>
```
- `enabled = True` is required. Without it, Remote Access doesn't start at all
  (from reading NVDA's source; not tested here).
- `host` carries the port. The `trustedCertificates` key is `host:port`, or just
  `host` when the port is 6837.
- `asc` writes the file with configobj, which leaves that key unquoted
  (`127.0.0.1:16837 = ...`). configobj reads the key the same with or without
  the quotes.
- **Pinning is silent on 2026.2**, which already has the fix for
  GHSA-m268-mc77-j2cr (fixed in 2026.1.1). A pre-seeded matching fingerprint
  connected with no dialog. Since the fix, NVDA checks the pinned fingerprint on
  every connection, so a changed cert brings up a trust dialog. `asc` must keep
  its relay cert stable for each config, or rewrite the fingerprint whenever the
  cert changes.
- The cert only needs to match the fingerprint; hostname checks don't apply.

Other settings the spike used, so NVDA starts unattended and silent:
```ini
[general]
showWelcomeDialogAtStartup = False
[update]
autoCheckForUpdates = False
askedAllowUsageStats = True
allowUsageStats = False
[speech]
synth = silence
[keyboard]
keyboardLayout = desktop
[virtualBuffers]
passThroughAudioIndication = True
```
NVDA took these without complaint and upgraded the file to `schemaVersion` 24.

## Route A facts (kept for watch mode)

If a user hosts the relay in their own NVDA:
- Its certificate is in `<configDir>\remoteAccess\localRelay\`, as
  `NvdaRemoteRelay.pem`, `.key` and `.fingerprint`. The `.fingerprint` file
  matched the SHA-256 of the cert the relay served.
- As leader, `channel_joined` lists the follower:
  `"clients":[{"id":1,"connection_type":"slave"}]`.
- Wrong channel key: per NVDA's source the relay sends
  `{"type":"error","message":"incorrect_password"}` and closes. Not observed: our
  test connection failed with a TLS error first.

## Running in Windows Sandbox (for M3)

- Windows Sandbox on this build has **no Edge** and **no `notepad.exe`**.
  `mshta.exe` exists, and NVDA reads an `.html` file opened in it in browse mode.
- **Silence the sandbox** by running `net stop audiosrv /y` at logon. Confirmed by the user: nothing was audible on the host in run 3. NVDA logs
  audio warnings but works, and still sends `wave` messages. Pressing the Volume
  Mute key from a script did not silence it.
- The `.wsb` `LogonCommand` worked with mapped folders: read-only for inputs,
  writable for results. A relocatable Python (uv-managed CPython) runs from a
  read-only mapped folder. The Microsoft Store Python can't be used that way.
- Creating the portable copy inside the sandbox took about 13 s, with
  `nvda_<ver>.exe --create-portable-silent --portable-path=C:\nvda`.
- **Closing:** force-killing the sandbox client while its VM was still running
  made the next launch attach to the session that was logging off ("The remote
  environment is logging off"). Wait until `vmmemWindowsSandbox` has exited
  before starting another sandbox.

## Observed by the e2e suite (M1 and M2, 2026-10-11)

`scripts/e2e_sandbox.py` runs `tests/e2e` against portable NVDA 2026.2 in
Windows Sandbox.
- NVDA pinned and accepted the relay cert `asc` generates (RSA 2048,
  self-signed, SAN `localhost` and `127.0.0.1`), joined, and sent speech. Keys
  sent through the daemon were released (Insert not held afterwards).
- With `[audio] soundVolumeFollowsVoice = False` and `soundVolume = 0`, NVDA
  still sends the `focusMode.wav` wave on Tab into an edit field. So `asc` can
  mute NVDA's own sounds and still track the mode.
- **A visible sandbox window takes keyboard focus on the host.** In one run,
  keys typed on the host (Alt+Tab) reached the sandbox and opened its task
  switcher in the middle of a test. With the window minimised the whole time,
  the tests passed, so the sandbox keeps working while minimised.

## Sandbox mode (M3, 2026-10-11)

Observed on Windows 11 Pro 26200 with the `wsb` CLI:
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
- Hidden (`ShowWindow` with `SW_HIDE`, no taskbar button) works as well as
  minimised: both sandbox e2e tests passed with the window hidden
  (2026-10-11).
- But hidden straight from the foreground, the client kept every key and click
  on the host until the sandbox closed (the user lost control of the PC,
  2026-10-11). Minimising first, then hiding, fixed it.
- The client also keeps focus once its window is minimised or hidden: the
  user's NVDA said "Windows Sandbox pane" and they had to Alt+Tab back. `asc`
  now notes the focused window before `wsb connect` and gives focus back to it
  while an out-of-sight client window has it (fixed, confirmed by the user).
- With networking on, the sandbox sits on the Hyper-V Default Switch subnet
  (here 172.24.82.2/20), and its gateway is the host's Default Switch address
  (172.24.80.1). NVDA inside reaches a relay bound to that address once one
  inbound firewall rule allows it (`asc sandbox-setup`). No firewall dialog
  appeared with the rule in place.
- **An app started through `wsb exec` doesn't get the foreground**, because of
  Windows' focus-stealing rules: `mshta` ran, but focus stayed on "Program
  Manager" and NVDA said nothing. `asc launch` therefore starts the app through
  a PowerShell script in the session that waits for the new window and brings it
  forward (`AttachThreadInput`, then `SetForegroundWindow`). With that, NVDA
  announced the window and the page.

## Your own NVDA (checked 2026-10-11)

- An installed NVDA's `nvda.exe` (`C:\Program Files\NVDA`) carries
  `uiAccess="True"` in its manifest and is byte-identical to
  `nvda_uiAccess.exe`; `nvda_noUIAccess.exe` sits next to it. A uiAccess exe
  normally has to be started through ShellExecute, not `CreateProcess`, so it
  wouldn't be `asc`'s child or sit in its kill-on-close job (not tried). And it
  can't run next to the user's NVDA anyway. So `asc` uses an installed NVDA only
  as it runs, through watch or shared mode.
- `nvda.exe`'s version resource holds the NVDA version (`FileVersion`
  `2026.2.0.57664`). `asc` reads it to refuse an NVDA older than 2025.1, which
  has no Remote Access.
- A portable NVDA keeps its config in `<portable>\userConfig`. `asc` copies it
  and rewrites `nvda.ini` on top; see `nvda_config.py`.

## NVDA 2025.1 (checked 2026-10-14)

`scripts/e2e_sandbox.py --nvda-version 2025.1` ran `tests/e2e/test_real_nvda.py` in
Windows Sandbox, with Windows Audio stopped as usual: all 6 tests failed.
- NVDA joined the relay, but asc never got any speech (`wait` timed out, not
  disconnected).
- NVDA's log: on joining, it plays a Remote Access cue; with no audio device,
  opening it raises `OSError: [WinError -2147023728] Element not found`
  (`nvwave.WavePlayer.open`). That exception ends `handleChannelJoined`
  early, and speech is never forwarded.
- NVDA 2026.2 in the same setup, the same day: all 6 passed.
- Sandbox mode also stops Windows Audio, so with 2025.1 it should hit the
  same failure (not observed: sandbox mode with 2025.1 was not run).
- Not tested: 2025.1 with an audio device, and versions between 2025.1 and
  2026.2.

## Open questions (not answered by the spike)

- The exact shape of a server `error` message (not seen on the wire).
- The `tone` message (not triggered).
- Behaviour on NVDA versions between 2025.1 and 2026.2 (not tested).
