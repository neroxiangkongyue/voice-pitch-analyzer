import ctypes
import os
import sys

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.LoadLibraryExW.restype = ctypes.c_void_p
_kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong]

for sp in sys.path:
    pyside6_dir = os.path.join(sp, "PySide6")
    if os.path.isdir(pyside6_dir):
        for dll in ["Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll"]:
            dll_path = os.path.join(pyside6_dir, dll)
            if os.path.exists(dll_path):
                _kernel32.LoadLibraryExW(dll_path, None, 0x00000008)
        os.environ["PATH"] = pyside6_dir + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(pyside6_dir)
        except Exception:
            pass
        break

from src.main_window import main

if __name__ == "__main__":
    main()
