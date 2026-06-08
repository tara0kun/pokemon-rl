# generic_agent 起動手順 (user 用)

このディレクトリは「Claude が Pokemon Emerald (および将来的に他タイトル) を プレイする 汎用 VLM agent」 のために独立した実装。

既存 training (`c:/pokemon-rl/pokemon_env.py`, `train.py`, port 8888/8889/8890) とは完全分離。 干渉しない。

mGBA CLI には起動時 lua auto-load 機能が無いため、 mGBA 自体の **起動と lua ロードだけは user の手動操作** が必要 (1 セッション 1 回)。 それ以外 (ボタン操作 / screenshot / RAM 読み取り / save / load) はすべて Claude が socket 経由で自律実行する。

---

## 1 回だけ手動で行う作業

### 1. mGBA を新規ウィンドウで起動

PowerShell:

```powershell
Start-Process "C:\Program Files\mGBA\mGBA.exe"
```

(既存 3 instance とは別の 4 つ目のウィンドウとして起動)

### 2. ROM を読み込む

mGBA メニュー: **File → Load ROM…**

選択: `c:\pokemon-rl\generic_agent\rom\emerald.gba`

(元の `C:\Users\tiita\ポケモン\1649 - Pokemon Emerald (J)(Independent).gba` には触らない。 generic_agent 用にコピー済み)

### 3. Lua script をロード

mGBA メニュー: **Tools → Scripting…**

開いた window で:
- **File → Load script…**
- 選択: `c:\pokemon-rl\generic_agent\scripts\mGBASocketServer_generic.lua`

console に次の行が出れば成功:

```
mGBA script server 0.8.1 ready. Listening on port 8895
```

### 4. (任意) Save state slot を確保

mGBA: **File → Save State → Slot 1**

generic_agent は Slot 1〜10 を自由に使う。 既存 training の Slot 1〜10 (port 8888/8889/8890 個別) とは別 process なので干渉しない。

---

## 起動完了の確認 (user 側)

PowerShell:

```powershell
cd c:\pokemon-rl
poke-rl\Scripts\python.exe -m generic_agent.smoke_test
```

期待出力:

```
[OK] port 8895 reachable
[OK] game title: POKEMON EMER
[OK] game code:  AGB-BPEJ
[OK] frame: <number>
[OK] screenshot saved: c:\pokemon-rl\generic_agent\logs\screens\smoke.png
[ALL OK]
```

ここまで通れば Week 1 (Brain LLM ループ) 実装に進める。

---

## 終了手順

mGBA window を閉じるだけ。 lua script は自動終了。

generic_agent 用 socket (port 8895) は process 終了で release される。

---

## 既存 training との干渉防止チェックリスト

- [x] port: 既存 8888/8889/8890 と被らない (= 8895)
- [x] ROM file: 元 file には書き込まない (コピーを使用)
- [x] lua script: 別 file (`mGBASocketServer_generic.lua`)
- [x] save state: 別 process なので Slot 番号は完全独立
- [x] pokemon_env.py の import 一切なし
- [x] exploration_map.json: read-only 参照のみ (信用度フラグ付き)
