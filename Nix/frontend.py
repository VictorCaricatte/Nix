"""
frontend.py — thin re-export wrapper.
Keeps backward-compat for any code that does `from frontend import Interface`.
"""

from tab_panel import InteractiveTerminal, ConnectionTab
from main_window import Interface, resource_path

__all__ = ["Interface", "ConnectionTab", "InteractiveTerminal", "resource_path"]
