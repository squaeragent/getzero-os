"""zero terminal design system — backward compatibility shim.

all styling now lives in console.py (Rich-powered).
this module re-exports everything for existing imports.
"""

from scanner.zeroos_cli.console import *  # noqa: F401,F403
from scanner.zeroos_cli.console import console, direction_icon, pnl, pnl_pct  # noqa: F401
