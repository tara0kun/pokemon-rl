# PC 再起動後 起動 checklist

**作成**: 2026-06-03 (autonomous session 末尾、 PC restart 準備)

## 状態 snapshot (PC restart 直前)

- mGBA: **全 3 instance 死亡** (PID 3304 / 23800 等、 10日連続稼働で hang)
- training: PID 80800 / 75200 既に停止 (clean shutdown 済)
- Slot saves: 6/02 22:01 頃に成功した **Slot 8 (8888 1F) / Slot 6 (8890 1F)** が永続保存中、 ROM 隣の .sav に格納
- exploration_map.json: cleanup #1+#2 + canon inject 適用済 (1F 完備 graph)、 復元用 backups あり

## 再起動後の手順

### 1. mGBA × 3 instance 起動

```powershell
# port 8888: Slot 8 (cave 1F (24,17) progressed state)
Start-Process "C:\Program Files\mGBA\mGBA.exe" -ArgumentList "<rom_path>"
# port 8889: 8889 mGBA は要 user 判断 (rule「kill しない」 範囲解釈)
# port 8890: Slot 6 (cave 1F (24,18) progressed state)
```

各 mGBA に対し:
- ROM 読み込み
- mGBA-http スクリプト (port 8888/8889/8890) を Lua console から起動
- 必要なら Slot 8 / 6 / 7 を File → Load State → Slot N で読み込み (ただし CLAUDE.md「saveStateLoad 禁止」 対象は env 内 load、 user の手動 load は OK)

### 2. mgba_http_pids.txt 更新

mGBA 起動後に PID を確認 → 反映:

```powershell
Get-Process mGBA | Select-Object Id | Out-File c:\pokemon-rl\mgba_http_pids.txt
```

### 3. socket 動作確認

```powershell
@(8888,8889,8890) | ForEach-Object {
  try { $tcp=New-Object Net.Sockets.TcpClient; $tcp.Connect("127.0.0.1",$_); "port $_ : OK"; $tcp.Close() }
  catch {"port $_ : FAIL"}
}
```

### 4. training 起動

```powershell
cd c:\pokemon-rl
PYTHONUNBUFFERED=1 poke-rl\Scripts\python.exe -u train.py > training_current.log 2>&1 &
```

Logs で「[Slot4] Restored checkpoint」 や「[MapChange] (0,0)→(24,7) pos=(24,18)」 を確認 = 1F state 復元 OK。

### 5. 定期 monitor cron 再設定

VSCode Claude Code 内で `/loop 10m` で 10分 cron 起動。 これで session 中の autonomous monitor が再開。

### 6. 最初の monitor 確認

```powershell
cd c:\pokemon-rl
poke-rl\Scripts\python.exe tools\monitor.py
```

期待: 8888/8890 が 1F (24,17)/(24,18) 周辺、 cleanup#1+#2+inject の効果で broad navigation 可能。

## トラブルシュート

- **socket FAIL**: mGBA-http lua script 未起動。 Tools → Scripting → Reload script。
- **Slot 8/6 が cave 内ではなく Littleroot**: save 失敗していた可能性、 元 ROM の自然 save から start (training が時間かけて再到達する)。
- **「exploration_map.json read error」**: backup から復元:
  - `cp exploration_map.json.backup_20260603_pre_warp_cleanup exploration_map.json` (cleanup 前に戻す、 ただし chronic stuck 再発リスク)
  - 推奨: 現状の cleanup 適用済 file をそのまま使う。

## 復元可能 backup 一覧

```
exploration_map.json.backup_pre_z302_20260529      (5/29 z302 前)
exploration_map.json.backup_20260603_pre_warp_cleanup  (6/03 cleanup#1 前)
exploration_map.json.backup_20260603_pre_warp_cleanup_v2  (6/03 cleanup#2 前)
exploration_map.json.bak_canon_GraniteCave_1F  (6/03 inject 前)
```

## 引継ぎ事項 (USER 判断必要)

1. nav target resolution design fix
2. 8889 mGBA 起動可否 (kill ルールの「dead から create」 解釈)
3. API key 再発行 (5/29 以降の宿題)
4. battle handler PP-zero auto-switch 修正許可
5. mGBA 定期再起動 protocol 設定 (10日連続稼働でhang問題)
