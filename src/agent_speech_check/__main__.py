"""`python -m agent_speech_check`, also the PyInstaller entry point."""

import sys

from agent_speech_check.cli import main

if __name__ == "__main__":
    sys.exit(main())
