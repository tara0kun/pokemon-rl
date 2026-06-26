"""Heuristic only infinite loop — mGBA always moving, training off."""
from __future__ import annotations
import subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
PY = ROOT / "poke-rl" / "Scripts" / "python.exe"
LOG = ROOT / "generic_agent" / "logs"
while True:
    tag = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    log_path = LOG / f"heur_loop_{tag}.log"
    cmd = [str(PY), "-X", "utf8", "-u", "-m",
           "generic_agent.claude_heuristic",
           "--turns", "1500", "--dataset"]
    with open(log_path, "w", encoding="utf-8", errors="replace") as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                       cwd=str(ROOT), timeout=3600, check=False)
    print(f"[{tag}] heuristic iter done, restarting...")
