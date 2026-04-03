# run.py
import sys
from gui.app import ExpGUI

use_mock = "--mock" in sys.argv
app = ExpGUI(use_mock=use_mock)
app.mainloop()