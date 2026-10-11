"""Agent Speech Check: let AI agents test your app with a real NVDA."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-speech-check")
except PackageNotFoundError:  # pragma: no cover - an exe built without --copy-metadata
    __version__ = "unknown"
