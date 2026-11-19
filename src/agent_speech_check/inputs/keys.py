"""Key names and gestures such as `NVDA+Shift+Down`, parsed into virtual-key codes for
NVDA and X keysyms for Orca.

Names are matched without regard to case. Unknown names raise `UnknownKey` with
close matches. Numpad keys are sent as the desktop layouts expect them, with
Num Lock off: `Numpad5` is VK_CLEAR and KP_Begin, not VK_NUMPAD5 and KP_5.
"""

from __future__ import annotations

import difflib
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from agent_speech_check.errors import BadGesture, UnknownKey, UntypableText
from agent_speech_check.system import winapi


@dataclass(frozen=True)
class Key:
    name: str
    vk: int
    extended: bool
    keysym: str


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
    "nvda": Key("NVDA", 0x2D, True, "Insert"),
    "control": Key("Control", 0x11, False, "Control_L"),
    "shift": Key("Shift", 0x10, False, "Shift_L"),
    "alt": Key("Alt", 0x12, False, "Alt_L"),
    "windows": Key("Windows", 0x5B, True, "Super_L"),
}

_NAMED: list[tuple[str, int, bool, str]] = [
    ("Tab", 0x09, False, "Tab"),
    ("Enter", 0x0D, False, "Return"),
    ("Escape", 0x1B, False, "Escape"),
    ("Space", 0x20, False, "space"),
    ("Backspace", 0x08, False, "BackSpace"),
    ("Delete", 0x2E, True, "Delete"),
    ("Insert", 0x2D, True, "Insert"),
    ("Home", 0x24, True, "Home"),
    ("End", 0x23, True, "End"),
    ("PageUp", 0x21, True, "Prior"),
    ("PageDown", 0x22, True, "Next"),
    ("Up", 0x26, True, "Up"),
    ("Down", 0x28, True, "Down"),
    ("Left", 0x25, True, "Left"),
    ("Right", 0x27, True, "Right"),
    ("CapsLock", 0x14, False, "Caps_Lock"),
    ("NumLock", 0x90, True, "Num_Lock"),
    ("ScrollLock", 0x91, False, "Scroll_Lock"),
    ("PrintScreen", 0x2C, True, "Print"),
    ("Pause", 0x13, False, "Pause"),
    ("Applications", 0x5D, True, "Menu"),
    # Numpad with Num Lock off, as NVDA's and Orca's desktop layouts use it.
    ("Numpad0", 0x2D, False, "KP_Insert"),
    ("Numpad1", 0x23, False, "KP_End"),
    ("Numpad2", 0x28, False, "KP_Down"),
    ("Numpad3", 0x22, False, "KP_Next"),
    ("Numpad4", 0x25, False, "KP_Left"),
    ("Numpad5", 0x0C, False, "KP_Begin"),
    ("Numpad6", 0x27, False, "KP_Right"),
    ("Numpad7", 0x24, False, "KP_Home"),
    ("Numpad8", 0x26, False, "KP_Up"),
    ("Numpad9", 0x21, False, "KP_Prior"),
    ("NumpadDelete", 0x2E, False, "KP_Delete"),
    ("NumpadEnter", 0x0D, True, "KP_Enter"),
    ("NumpadPlus", 0x6B, False, "KP_Add"),
    ("NumpadMinus", 0x6D, False, "KP_Subtract"),
    ("NumpadMultiply", 0x6A, False, "KP_Multiply"),
    ("NumpadDivide", 0x6F, True, "KP_Divide"),
    ("Comma", 0xBC, False, "comma"),
    ("Period", 0xBE, False, "period"),
    ("Slash", 0xBF, False, "slash"),
    ("Backslash", 0xDC, False, "backslash"),
    ("Semicolon", 0xBA, False, "semicolon"),
    ("Apostrophe", 0xDE, False, "apostrophe"),
    ("LeftBracket", 0xDB, False, "bracketleft"),
    ("RightBracket", 0xDD, False, "bracketright"),
    ("Minus", 0xBD, False, "minus"),
    ("Equals", 0xBB, False, "equal"),
    ("Grave", 0xC0, False, "grave"),
]

ALIASES: dict[str, str] = {
    "orca": "nvda",  # the screen reader's own modifier, Insert on the desktop layout
    "ctrl": "control",
    "win": "windows",
    "super": "windows",
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
    keys = {name.lower(): Key(name, vk, ext, sym) for name, vk, ext, sym in _NAMED}
    keys.update(MODIFIERS)
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        keys[letter.lower()] = Key(letter, ord(letter), False, letter.lower())
    for digit in "0123456789":
        keys[digit] = Key(digit, ord(digit), False, digit)
    for n in range(1, 25):
        keys[f"f{n}"] = Key(f"F{n}", 0x6F + n, False, f"F{n}")
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


_SHIFT_BITS = ((1, "shift"), (2, "control"), (4, "alt"))
_MAX_UTF16_UNIT = 0xFFFF
_TYPED_SPECIALS = {"\n": "enter", "\t": "tab"}


def _gestures(text: str, one: Callable[[str], Gesture | None], why: str) -> tuple[Gesture, ...]:
    """Every character is checked first; if any can't be typed, nothing is."""
    gestures: list[Gesture] = []
    bad: list[str] = []
    for char in text.replace("\r\n", "\n"):
        special = _TYPED_SPECIALS.get(char)
        gesture = Gesture((), KEYS[special]) if special is not None else one(char)
        if gesture is None:
            bad.append(char)
        else:
            gestures.append(gesture)
    if bad:
        raise UntypableText(list(dict.fromkeys(bad)), why)
    return tuple(gestures)


def text_gestures(text: str) -> tuple[Gesture, ...]:
    """One gesture per character, from the current keyboard layout."""
    return _gestures(text, _from_layout, "with the current keyboard layout")


def _from_layout(char: str) -> Gesture | None:
    scan = _scan(char)
    if scan is None:
        return None
    vk, state = scan
    modifiers = tuple(MODIFIERS[name] for bit, name in _SHIFT_BITS if state & bit)
    return Gesture(modifiers, Key(char, vk, False, _unicode_keysym(char)))


def _scan(char: str) -> tuple[int, int] | None:
    # VkKeyScanW takes one UTF-16 unit, so characters outside the BMP can't be typed.
    if ord(char) > _MAX_UTF16_UNIT:
        return None
    return winapi.vk_key_scan(char)


def typed_gestures(text: str) -> tuple[Gesture, ...]:
    """One gesture per character, for X: the Unicode keysym names the character,
    and xdotool maps it to a key even when the layout has none."""
    return _gestures(text, _for_x, "(control characters)")


def _for_x(char: str) -> Gesture | None:
    if unicodedata.category(char) in ("Cc", "Cs"):
        return None
    return Gesture((), Key(char, 0, False, _unicode_keysym(char)))


def _unicode_keysym(char: str) -> str:
    return f"U{ord(char):04X}"
