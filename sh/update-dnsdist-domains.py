#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


INSTALLED_LIBRARY = Path("/usr/local/lib/dnsdist-automation")
SOURCE_LIBRARY = Path(__file__).resolve().parent
for candidate in (INSTALLED_LIBRARY, SOURCE_LIBRARY):
    if (candidate / "dnsdist_automation").is_dir():
        sys.path.insert(0, str(candidate))
        break

from dnsdist_automation.domains import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
