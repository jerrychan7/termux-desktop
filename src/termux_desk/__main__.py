"""Run desk as a module: python3 -m termux_desk <command>."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
