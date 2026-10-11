"""Agent Speech Check: let AI agents test your app with a real NVDA."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-speech-check")
except PackageNotFoundError:  # pragma: no cover - running from a PyInstaller exe without metadata
    __version__ = "0.0.1"
