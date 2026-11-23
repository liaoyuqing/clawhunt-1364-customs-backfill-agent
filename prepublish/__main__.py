"""python -m prepublish -> 等同于 python -m prepublish.check。"""

from __future__ import annotations

import sys

from .check import main

if __name__ == "__main__":
    sys.exit(main())
