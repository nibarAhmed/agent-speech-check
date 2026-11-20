"""Agent Speech Check: let AI agents test your app with a real screen reader (NVDA or Orca)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-speech-check")
except PackageNotFoundError:  # an exe built without --copy-metadata
    __version__ = "unknown"
