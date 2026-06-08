"""
セーブステート作成スクリプト
============================
【手順】
  1. mGBA でエメラルドを起動し、ゲームを「プレイを始めたい地点」まで進める
     （例: 最初のポケモンを受け取った直後、ポケモンセンターの前など）
  2. mGBA を一時停止せずにこのスクリプトを実行する
  3. スロット1にセーブステートが保存される
  4. 以後 train.py を実行すると、毎エピソードここからリセットされる

【注意】
  - mGBAとmGBA-httpが起動している必要があります
  - 一度保存したら、train.py を途中で止めても再実行すれば同じ地点から再開します
"""

import requests
import sys

BASE_URL = "http://localhost:5000"


def check_connection():
    try:
        r = requests.get(f"{BASE_URL}/index.html", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def save_to_slot(slot: int = 1):
    if not check_connection():
        print("エラー: mGBA-http に接続できません。")
        print("  ・mGBA が起動しているか確認してください")
        print("  ・mGBASocketServer.lua が読み込まれているか確認してください")
        print("  ・mGBA-http.exe が起動しているか確認してください")
        sys.exit(1)

    print(f"スロット {slot} にセーブステートを保存します...")
    try:
        r = requests.post(
            f"{BASE_URL}/core/savestateslot",
            params={"slot": slot},
            timeout=5,
        )
        print(f"保存完了！ (ステータスコード: {r.status_code})")
        print(f"これで train.py を実行すると、このゲーム状態からAIのトレーニングが始まります。")
    except Exception as e:
        print(f"エラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    save_to_slot(slot=1)
