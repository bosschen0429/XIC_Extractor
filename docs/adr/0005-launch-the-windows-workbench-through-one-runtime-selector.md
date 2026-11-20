---
status: accepted
---

# Launch the Windows Workbench through one runtime selector

Double-clicking should open the browser, not a console. Dragged paths must
survive spaces, ampersands and literal percent signs. The VBScript launcher
therefore delegates Python/uv selection to the same batch entry point used
from a terminal; it does not choose a second runtime.

WScript.Run expands environment references in its command string. Keep user
arguments in process environment variables and write the references, quoted,
to a temporary batch file. Run that file with delayed expansion off so cmd
expands the arguments once. A quote inside an argument cannot be represented
by this interface and is rejected. See the [launcher](../../launch_gui.vbs).
