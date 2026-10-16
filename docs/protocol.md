# NVDA Remote Access protocol as used by `asc`

This is an implementation reference for the subset of NVDA Remote Access used by `asc`. There is no official protocol specification; the details below are based on observation and limited source inspection, not a complete or version-independent contract. `asc` does not copy or import NVDA code. How `asc` runs NVDA around it (Windows Sandbox, focus, versions) is in [findings.md](findings.md).

## Evidence and test baseline

Unless a section says otherwise, wire behavior was observed with portable NVDA 2026.2 in Windows Sandbox on Windows 11 Pro (build 26200), in experiments and with the end-to-end suite.

- **Observed** means seen on the wire or reproduced in the stated test setup.
- **Source-derived, not observed** means inferred from reading NVDA source, but not seen directly on the wire.
- **Not tested** means the behavior is unknown; do not rely on it as a compatibility guarantee.

## Terms

| Term | Meaning | On the wire |
|---|---|---|
| relay | the TLS server both sides connect to. It forwards messages between the members of a channel. `asc` runs it. | |
| channel | a shared random key. Connections that join the same channel hear each other. | `"channel": "<key>"` |
| follower | the controlled side: NVDA, which sends speech and receives keys | `"connection_type": "slave"` |
| leader | the controlling side: `asc`, which receives speech and sends keys | `"connection_type": "master"` |

## Why `asc` hosts the relay

`asc` hosts the relay and NVDA connects **out** to it as the follower.

Letting NVDA host the relay (`selfHosted = True`) is rejected:
- NVDA's relay listens on **all interfaces**: `0.0.0.0:6837` and `[::]:6837`.
  Anyone on the network who knows the key could drive it.
- Its first listen raised a **Windows Firewall dialog** ("Do you want to allow
  public and private networks to access this app? ... NVDA application (no UI
  Access)"). NVDA read the dialog aloud, and it took focus away from the app
  under test. Unattended runs can't get past it.

With `asc` as the relay, NVDA opened no listening port at all (`netstat` showed
nothing on 6837), and no dialog appeared.

## Transport

- TLS, then UTF-8 JSON objects, one per line, terminated by `\n`.
- Key order inside objects varies; parse by name.
- Default port 6837. Any port works if `host` names it.

## Connection lifecycle

### Handshake (follower NVDA to our relay)

What NVDA does on startup with the config in
[Follower configuration](#follower-configuration-nvdaini):

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

The whole thing takes about 2 s from NVDA's start. So the relay must accept
connections that fail the TLS handshake or close straight away, and keep
listening.

### Reconnect

When the TCP connection drops, NVDA reconnects about 5 s later and repeats
steps 3 to 5.

### Leaving and rejoining without disconnecting

We can tell NVDA that the leader left or rejoined over the same connection:
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

### Mode switches (browse and focus)

With `[virtualBuffers] passThroughAudioIndication = True` (NVDA's default):
- **Into focus mode:** a `wave` with `focusMode.wav`. Seen on Tab into an edit
  field, and on NVDA+Space.
- **Into browse mode:** a `wave` with `browseMode.wav`. Seen on Escape out of the
  field, and on NVDA+Space.
- **Page load:** no wave. Loading a page in browse mode sent no sound, so
  `asc` reports the mode as `unknown` until the first switch.

When that setting is off, NVDA speaks "focus mode" or "browse mode" instead (not
observed).

With `[audio] soundVolumeFollowsVoice = False` and `soundVolume = 0`, NVDA still
sends the `focusMode.wav` wave. So `asc` can mute NVDA's own sounds and still
track the mode.

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

### Held keys are NOT released for us

- After Shift down, a `client_left` for the leader left Shift **held**.
- After Shift down, dropping the TCP connection left Shift **held**. It stayed
  held after NVDA reconnected, until we sent `pressed: false`.

So `asc` must release keys itself: every press goes through one function that
guarantees the release. After a reconnect, the daemon first sends a release for
every key it may have left down.

## Errors

- **Certificate not trusted:** NVDA drops the connection and retries, as in
  steps 1 to 3 of the handshake. A relay must treat a failed TLS handshake as
  normal.
- **Connection dropped:** NVDA reconnects about 5 s later. Keys it held stay
  held (see above).
- **Error message:** NVDA's source has a relay `error` message, for example
  `{"type":"error","message":"incorrect_password"}` for a wrong channel key
  (source-derived, not observed). `asc`'s relay never sends one.
- **Unknown types:** a newer NVDA may send types not listed here. `asc` ignores
  them instead of failing.

## Follower configuration (`nvda.ini`)

For an NVDA that `asc` launches:
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
  (from reading NVDA's source; not observed).
- `host` carries the port. The `trustedCertificates` key is `host:port`, or just
  `host` when the port is 6837.
- `asc` writes the file with configobj, which leaves that key unquoted
  (`127.0.0.1:16837 = ...`). configobj reads the key the same with or without
  the quotes.

Other settings, so NVDA starts unattended and silent:
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

## Security considerations

- Whoever reaches the relay and knows the channel key can send keys to NVDA.
  So the relay listens on 127.0.0.1, or for sandbox mode on the host's
  Hyper-V Default Switch address, never on all interfaces, and the key is
  random.
- **Pinning is silent on 2026.2**, which already has the fix for
  GHSA-m268-mc77-j2cr (fixed in 2026.1.1). A pre-seeded matching fingerprint
  connected with no dialog. Since the fix, NVDA checks the pinned fingerprint on
  every connection, so a changed cert brings up a trust dialog. `asc` must keep
  its relay cert stable for each config, or rewrite the fingerprint whenever the
  cert changes.
- The cert only needs to match the fingerprint; hostname checks don't apply.
  NVDA accepts the cert `asc` generates (RSA 2048, self-signed, SAN `localhost`
  and `127.0.0.1`).

## Version compatibility

- `asc` needs NVDA 2026.2 or later, the version all of the above was observed
  with.
- 2025.1, the first with Remote Access built in, joins the relay but forwards
  no speech when the machine has no audio device. Details in
  [findings.md](findings.md#nvda-20251).
- 2025.1 with an audio device, and versions between 2025.1 and 2026.2, are not
  tested.
