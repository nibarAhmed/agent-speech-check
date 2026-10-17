"""Names vulture reports as unused that are used (by argparse, pytest, etc.)."""

from vulture.whitelist_utils import Whitelist

_ = Whitelist()

# HTMLParser calls these hooks (keyref.py).
_.handle_starttag
_.handle_endtag
_.handle_data

# Read only to check NVDA's message (protocol.py).
_.hz
_.paused

# ctypes struct fields, read by Windows (winapi.py).
_.LimitFlags
_.cbSize
_.lpfnWndProc
_.hInstance
_.lpszClassName
_.hWnd
_.uID
_.uFlags
_.uCallbackMessage
_.hIcon
_.szTip
_.uVersion

# Tests post the messages Windows would send (winapi.py).
_.post
