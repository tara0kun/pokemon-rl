"""
generic_agent settings.

既存 training (port 8888/8889/8890) と完全分離。
このファイルだけが path / port / model の唯一の真実源。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ROM_PATH = ROOT / "rom" / "emerald_en.gba"
ROM_PATH_JP = ROOT / "rom" / "emerald.gba"
LUA_SCRIPT_PATH = ROOT / "scripts" / "mGBASocketServer_generic.lua"

MGBA_EXE = Path(r"C:\Program Files\mGBA\mGBA.exe")

SOCKET_HOST = "127.0.0.1"
SOCKET_PORT = 8895

SCREENSHOT_DIR = ROOT / "logs" / "screens"
MEMORY_DIR = ROOT / "memory"
LOG_DIR = ROOT / "logs"

SOCKET_TERMINATOR = "<|END|>"
SOCKET_SUCCESS = "<|SUCCESS|>"
SOCKET_ERROR = "<|ERROR|>"
SOCKET_ACK = "<|ACK|>"

SOCKET_TIMEOUT_SEC = 5.0
SOCKET_CONNECT_RETRIES = 3

MODEL_BRAIN = "claude-opus-4-8"
MAX_OUTPUT_TOKENS = 512


def ensure_runtime_dirs() -> None:
    for d in (SCREENSHOT_DIR, MEMORY_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_api_key() -> str:
    """ANTHROPIC_API_KEY を環境変数から、 なければ Windows User scope から読む。

    setx 後 VSCode 再起動が反映していない場合の fallback。
    key 自体はログ / ファイルに残さない。
    """
    k = os.environ.get("ANTHROPIC_API_KEY", "")
    if k.startswith("sk-ant-"):
        return k
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[System.Environment]::GetEnvironmentVariable("
                "'ANTHROPIC_API_KEY', 'User')",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        k = result.stdout.strip()
        if k.startswith("sk-ant-"):
            os.environ["ANTHROPIC_API_KEY"] = k
            return k
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError(
        "ANTHROPIC_API_KEY not found in env or Windows User scope. "
        "Run: setx ANTHROPIC_API_KEY \"<key>\" and restart VSCode."
    )
