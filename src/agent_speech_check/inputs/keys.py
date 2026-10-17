"""Key names and gestures such as `NVDA+Shift+Down`, parsed into virtual-key codes.

Names are matched without regard to case. Unknown names raise `UnknownKey` with
close matches. Numpad keys are sent as NVDA's desktop layout expects them, with
Num Lock off: `Numpad5` is VK_CLEAR, not VK_NUMPAD5.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from agent_speech_check.errors import BadGesture, UnknownKey, UntypableText
from agent_speech_check.windows import winapi


@dataclass(frozen=True)
class Key:
    name: str
    vk: int
    extended: bool


@dataclass(frozen=True)
class Gesture:
    """Modifiers go down in order, then the key; releases come in reverse."""

    modifiers: tuple[Key, ...]
    key: Key

    @property
    def strokes(self) -> tuple[Key, ...]:
        return (*self.modifiers, self.key)

    def __str__(self) -> str:
        return "+".join(k.name for k in self.strokes)


MODIFIERS: dict[str, Key] = {
    "nvda": Key("NVDA", 0x2D, True),
    "control": Key("Control", 0x11, False),
    "shift": Key("Shift", 0x10, False),
    "alt": Key("Alt", 0x12, False),
    "windows": Key("Windows", 0x5B, True),
}

_NAMED: list[tuple[str, int, bool]] = [
    ("Tab", 0x09, False),
    ("Enter", 0x0D, False),
    ("Escape", 0x1B, False),
    ("Space", 0x20, False),
    ("Backspace", 0x08, False),
    ("Delete", 0x2E, True),
    ("Insert", 0x2D, True),
    ("Home", 0x24, True),
    ("End", 0x23, True),
    ("PageUp", 0x21, True),
    ("PageDown", 0x22, True),
    ("Up", 0x26, True),
    ("Down", 0x28, True),
    ("Left", 0x25, True),
    ("Right", 0x27, True),
    ("CapsLock", 0x14, False),
    ("NumLock", 0x90, True),
    ("ScrollLock", 0x91, False),
    ("PrintScreen", 0x2C, True),
    ("Pause", 0x13, False),
    ("Applications", 0x5D, True),
    # Numpad with Num Lock off, as NVDA's desktop layout uses it.
    ("Numpad0", 0x2D, False),
    ("Numpad1", 0x23, False),
    ("Numpad2", 0x28, False),
    ("Numpad3", 0x22, False),
    ("Numpad4", 0x25, False),
    ("Numpad5", 0x0C, False),
    ("Numpad6", 0x27, False),
    ("Numpad7", 0x24, False),
    ("Numpad8", 0x26, False),
    ("Numpad9", 0x21, False),
    ("NumpadDelete", 0x2E, False),
    ("NumpadEnter", 0x0D, True),
    ("NumpadPlus", 0x6B, False),
    ("NumpadMinus", 0x6D, False),
    ("NumpadMultiply", 0x6A, False),
    ("NumpadDivide", 0x6F, True),
    ("Comma", 0xBC, False),
    ("Period", 0xBE, False),
    ("Slash", 0xBF, False),
    ("Backslash", 0xDC, False),
    ("Semicolon", 0xBA, False),
    ("Apostrophe", 0xDE, False),
    ("LeftBracket", 0xDB, False),
    ("RightBracket", 0xDD, False),
    ("Minus", 0xBD, False),
    ("Equals", 0xBB, False),
    ("Grave", 0xC0, False),
]

ALIASES: dict[str, str] = {
    "ctrl": "control",
    "win": "windows",
    "esc": "escape",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "uparrow": "up",
    "downarrow": "down",
    "leftarrow": "left",
    "rightarrow": "right",
    "apps": "applications",
    "menu": "applications",
    "printscr": "printscreen",
    "back": "backspace",
}


def _all_keys() -> dict[str, Key]:
    keys = {name.lower(): Key(name, vk, ext) for name, vk, ext in _NAMED}
    keys.update(MODIFIERS)
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        keys[letter.lower()] = Key(letter, ord(letter), False)
    for digit in "0123456789":
        keys[digit] = Key(digit, ord(digit), False)
    for n in range(1, 25):
        keys[f"f{n}"] = Key(f"F{n}", 0x6F + n, False)
    return keys


KEYS = _all_keys()


def parse_key(name: str) -> Key:
    lowered = name.strip().lower()
    lowered = ALIASES.get(lowered, lowered)
    key = KEYS.get(lowered)
    if key is None:
        choices = [*KEYS, *ALIASES]
        close = difflib.get_close_matches(lowered, choices, n=3, cutoff=0.6)
        raise UnknownKey(name, [KEYS[ALIASES.get(c, c)].name for c in dict.fromkeys(close)])
    return key


def parse_gesture(text: str) -> Gesture:
    parts = text.split("+")
    if any(not part.strip() for part in parts):
        raise BadGesture(text, "empty key name; join keys with + as in NVDA+Tab")
    keys = [parse_key(part) for part in parts]
    *modifiers, key = keys
    for modifier in modifiers:
        if modifier not in MODIFIERS.values():
            raise BadGesture(
                text, f"{modifier.name} is not a modifier (NVDA, Control, Shift, Alt, Windows)"
            )
    if len(set(keys)) != len(keys):
        raise BadGesture(text, "a key is repeated")
    return Gesture(tuple(modifiers), key)


@dataclass(frozen=True)
class Hotkey:
    """A system-wide hotkey, as RegisterHotKey takes it."""

    text: str
    modifiers: int
    vk: int


# The panic hotkey unless `panic_key` in config.toml names another.
PANIC_KEY = "Control+Shift+Alt+F12"
# NVDA isn't here: RegisterHotKey knows only these four modifiers.
_HOTKEY_MODIFIERS = {
    "Control": winapi.MOD_CONTROL,
    "Shift": winapi.MOD_SHIFT,
    "Alt": winapi.MOD_ALT,
    "Windows": winapi.MOD_WIN,
}


def parse_hotkey(text: str) -> Hotkey:
    gesture = parse_gesture(text)
    if any(key.name == "NVDA" for key in gesture.strokes):
        raise BadGesture(text, "NVDA can't be part of a hotkey; use Control, Shift, Alt, Windows")
    if gesture.key in MODIFIERS.values():
        raise BadGesture(text, f"{gesture.key.name} is a modifier, so it can't end a hotkey")
    # Shift alone isn't enough: Shift+A would catch every capital A anyone types.
    if all(modifier.name == "Shift" for modifier in gesture.modifiers):
        raise BadGesture(
            text, "a hotkey needs Control, Alt or Windows, so it doesn't take keys from apps"
        )
    modifiers = 0
    for modifier in gesture.modifiers:
        modifiers |= _HOTKEY_MODIFIERS[modifier.name]
    return Hotkey(str(gesture), modifiers, gesture.key.vk)


def parse_gestures(texts: list[str]) -> tuple[Gesture, ...]:
    """Parse every gesture before any is sent."""
    return tuple(parse_gesture(text) for text in texts)


_SHIFT_BITS = ((1, "shift"), (2, "control"), (4, "alt"))
_MAX_UTF16_UNIT = 0xFFFF
_TYPED_SPECIALS = {"\n": "enter", "\t": "tab"}


def text_gestures(text: str) -> tuple[Gesture, ...]:
    """One gesture per character, from the current keyboard layout.

    Every character is checked first; if any can't be typed, nothing is.
    """
    gestures: list[Gesture] = []
    bad: list[str] = []
    for char in text.replace("\r\n", "\n"):
        special = _TYPED_SPECIALS.get(char)
        scan = None if special else _scan(char)
        if special is not None:
            gestures.append(Gesture((), KEYS[special]))
        elif scan is None:
            bad.append(char)
        else:
            vk, state = scan
            modifiers = tuple(MODIFIERS[name] for bit, name in _SHIFT_BITS if state & bit)
            gestures.append(Gesture(modifiers, Key(char, vk, extended=False)))
    if bad:
        raise UntypableText(list(dict.fromkeys(bad)))
    return tuple(gestures)


def _scan(char: str) -> tuple[int, int] | None:
    # VkKeyScanW takes one UTF-16 unit, so characters outside the BMP can't be typed.
    if ord(char) > _MAX_UTF16_UNIT:
        return None
    return winapi.vk_key_scan(char)
