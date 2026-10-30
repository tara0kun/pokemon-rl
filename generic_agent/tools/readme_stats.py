"""README 冒頭の「現在地と数値」ブロックを、リポジトリの中身から作る。

手で書き写した数値は古くなる。2026-10 時点で README は「Gym 1 に挑戦中」のまま、
面接台本の行数は 07 月の値だった。数値はここで計算し、
test_readme_stats が README のブロックと一致しているかを確かめる（ずれたら CI が赤になる）。

現在地とマイルストーンの正本は STATUS.md。ここはそれを写すだけで、自分では決めない。

    poke-rl/Scripts/python.exe -m generic_agent.tools.readme_stats          # 生成結果を表示
    poke-rl/Scripts/python.exe -m generic_agent.tools.readme_stats --write  # README を書き換える
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
STATUS = ROOT / "STATUS.md"
BEGIN = "<!-- readme-stats:begin（generic_agent/tools/readme_stats.py が生成。手で直さない） -->"
END = "<!-- readme-stats:end -->"


def code_lines() -> int:
    """tests を除く generic_agent の .py の行数（空行・コメント込み）。

    git の追跡対象だけを数える。手元にだけある未追跡のファイルで、手元と CI の値が変わらないように。
    """
    out = subprocess.run(["git", "ls-files", "generic_agent/*.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.split()
    files = [f for f in out if "/tests/" not in f]
    return sum(len((ROOT / f).read_text(encoding="utf-8", errors="replace").splitlines()) for f in files)


def test_count() -> int:
    """CLAUDE.md のテスト手順と同じ discover で数える（skip されるものも件数に入る）。"""
    suite = unittest.defaultTestLoader.discover(str(ROOT / "generic_agent" / "tests"), top_level_dir=str(ROOT))
    return suite.countTestCases()


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise ValueError(f"STATUS.md に「## {heading}」節が無い")
    return m.group(1)


def status_facts() -> dict[str, str]:
    """STATUS.md から、現在地の 1 行目・最新のマイルストーン・Last verified を抜き出す。"""
    text = STATUS.read_text(encoding="utf-8")
    now = re.search(r"^- \*\*(.+?)\*\*", _section(text, "現在地"), re.M)
    rows = re.findall(r"^\| `([^`]+)` \| ([^|]+?) \| (.+?) \|$", _section(text, "最良の結果"), re.M)
    verified = re.search(r"Last verified: ([0-9-]+)", text)
    if not (now and rows and verified):
        raise ValueError("STATUS.md の現在地・最良の結果・Last verified のどれかが読めない")
    tag, date, what = rows[-1]
    return {"now": now.group(1), "tag": tag, "date": date, "what": re.sub(r"（.*?）", "", what),
            "verified": verified.group(1)}


def _about(n: int, step: int) -> int:
    """step 単位に四捨五入する。

    正確な値を README に書くと、コードを 1 行変えるたびにテストが落ちて CI が赤になり、
    赤のまま放置されやすくなる。丸めておけば普段の commit では変わらず、
    「9,900 行」のような大きなずれだけを落とせる。
    """
    return int(n / step + 0.5) * step


def render() -> str:
    s = status_facts()
    lines = [
        BEGIN,
        "| 項目 | 値 |",
        "|---|---|",
        f"| 現在地 | {s['now']} |",
        f"| 最新のマイルストーン | {s['what']}（`{s['tag']}`、{s['date']}） |",
        f"| コード量 | `generic_agent/` 約 {_about(code_lines(), 1000):,} 行（tests を除く .py、空行・コメント込み） |",
        f"| テスト | 約 {_about(test_count(), 50)} 件（`unittest discover`、CI で毎回実行） |",
        "",
        f"現在地の正本と次の一手は [STATUS.md](STATUS.md)（Last verified: {s['verified']}）。",
        END,
    ]
    return "\n".join(lines)


def current_block(readme: str) -> str | None:
    m = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), readme, re.S)
    return m.group(0) if m else None


def main() -> int:
    # Windows の既定コンソール（cp932）でも日本語の表示が崩れないようにする
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="README のブロックを書き換える")
    args = ap.parse_args()
    block = render()
    if not args.write:
        print(block)
        return 0
    readme = README.read_text(encoding="utf-8")
    old = current_block(readme)
    if old is None:
        print(f"README に {BEGIN} が無い", file=sys.stderr)
        return 1
    README.write_text(readme.replace(old, block), encoding="utf-8")
    print("README を更新した" if old != block else "README は最新")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
