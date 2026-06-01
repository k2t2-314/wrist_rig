"""
Compatibility shim for older imports.

The canonical GUI implementation now lives in gui/app.py.
"""

from gui.app import ExpGUI


__all__ = ["ExpGUI"]
