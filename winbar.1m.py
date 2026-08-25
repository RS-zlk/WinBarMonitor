#!/usr/bin/env python3
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
from pathlib import Path
import sys

plugin_dir = Path(__file__).resolve().parent
installed_library = plugin_dir / ".winbar_lib"
sys.path.insert(0, str(installed_library if installed_library.is_dir() else plugin_dir))
from winbar_monitor import main

raise SystemExit(main())
