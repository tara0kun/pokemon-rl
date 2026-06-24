"""Autonomous mGBA launch + Lua + savestate restore.

Recover from mGBA crash without user intervention. Launches mGBA with
ROM + savestate via CLI, then uses Windows UI Automation (uiautomation
package) to navigate File → Load recent script → first item to load
the socket-server Lua script. Verifies port 8895 is open at the end.

Usage:
    python -m generic_agent.tools.mgba_autolaunch [SAVESTATE]

If SAVESTATE omitted, uses the most recent milestone savestate that
matches the highest progression map (e.g. Rustboro Gym).
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

MGBA_EXE = r"C:\Program Files\mGBA\mGBA.exe"
ROM = r"C:\pokemon-rl\generic_agent\rom\emerald_en.gba"
LUA = r"C:\pokemon-rl\generic_agent\scripts\mGBASocketServer_generic.lua"
SAVESTATE_DIR = Path(r"C:\pokemon-rl\generic_agent\memory\curriculum_savestates")
PORT = 8895


def pick_savestate() -> str:
    """Pick highest-progression savestate (Rustboro Gym preferred)."""
    candidates = list(SAVESTATE_DIR.glob("milestone_*.ss1"))
    if not candidates:
        raise FileNotFoundError(f"No savestates in {SAVESTATE_DIR}")
    # Prefer Rustboro Gym (m=11,3) - Roxanne battle position
    for c in candidates:
        if "m11_3" in c.name:
            return str(c)
    # Fallback to most recently modified
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def port_open(port: int = PORT, timeout: float = 0.5) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def kill_existing_mgba() -> None:
    subprocess.run(
        ["powershell", "-Command",
         "Get-Process mGBA -ErrorAction SilentlyContinue | Stop-Process -Force"],
        capture_output=True,
    )
    time.sleep(2)


def launch_mgba(savestate: str) -> None:
    subprocess.Popen([MGBA_EXE, ROM, "-t", savestate])
    time.sleep(8)


def load_lua_via_ui() -> bool:
    """Navigate mGBA Tools → Scripting → File → Load recent script → first."""
    import uiautomation as auto
    mw = auto.WindowControl(searchDepth=2, RegexName="mGBA.*Emerald.*")
    if not mw.Exists(3):
        print("mGBA window not found")
        return False
    mw.SetActive()
    time.sleep(0.5)

    scripting = auto.MenuItemControl(searchDepth=10, Name="Scripting...")
    if not scripting.Exists(2):
        print("Scripting menu item not found")
        return False
    scripting.Click()
    time.sleep(2)

    sw = auto.WindowControl(searchDepth=4, Name="Scripting")
    if not sw.Exists(3):
        print("Scripting window did not open")
        return False
    sw.SetActive()
    time.sleep(0.5)

    file_item = auto.MenuItemControl(searchDepth=5, Name="File")
    if not file_item.Exists(2):
        print("File menu not found")
        return False
    file_item.GetExpandCollapsePattern().Expand()
    time.sleep(1)

    recent = auto.MenuItemControl(searchDepth=10, Name="Load recent script")
    if not recent.Exists(2):
        print("Load recent script not found")
        return False
    recent.GetExpandCollapsePattern().Expand()
    time.sleep(1.5)

    found = [None]

    def find_lua(node, depth=0):
        if depth > 8 or found[0]:
            return
        for c in node.GetChildren():
            if c.Name and c.Name.lower().endswith(".lua"):
                found[0] = c
                return
            find_lua(c, depth + 1)

    find_lua(auto.GetRootControl())
    if not found[0]:
        print("No .lua recent item found")
        return False
    print(f"Invoking: {found[0].Name}")
    found[0].GetInvokePattern().Invoke()
    time.sleep(3)
    return True


def main() -> int:
    savestate = sys.argv[1] if len(sys.argv) > 1 else pick_savestate()
    print(f"savestate: {savestate}")

    kill_existing_mgba()
    launch_mgba(savestate)

    if not load_lua_via_ui():
        print("Lua load failed")
        return 1

    if port_open():
        print(f"PORT {PORT} OPEN — mGBA + Lua + savestate ready")
        return 0
    print(f"PORT {PORT} closed after load")
    return 2


if __name__ == "__main__":
    sys.exit(main())
