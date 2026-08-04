# ESP32-C3＋SEN66一体型PCB Rev.A 計画中止

- **決定日:** 2026-08-04
- **ステータス:** **CANCELLED**
- **対象:** OMK ESP32-C3＋SEN66一体型PCB Rev.A（メイン基板およびSEN66キャリア）

## 決定

ESP32-C3モジュール、USB-C、USB ESD、AP63203降圧電源、無線アンテナおよびSEN66接続を一体化する現行Rev.A PCB計画を中止する。これは一時停止・保留・再開待ちではなく、現行アーキテクチャに対する計画中止である。

**Manufacturing release: CANCELLED. Fabrication permitted: NO.**

## 理由

目的は、SEN66をESP32系デバイスへ接続し、Wi-Fi/MQTTでデータ送信することである。専用一体型基板では、USB、電源、書込み、ESD、アンテナ、差動配線および製造検証までを同時に扱う必要があり、この目的に対して設計範囲が過大になった。

今後の構成は未決定とする。USB、電源、書込み回路および無線アンテナは既製ESP32系デバイスへ委ね、OMK側で新規製作する場合もSEN66接続用の最小変換基板に限定する方針を検討する。M5StickS3を含む完成品・開発ボードは候補の一つであり、採用決定ではない。

## 到達点と未完了事項

完了済みの履歴成果は、Phase 1基本設計、両回路図、ERC、SEN66キャリア、主要部品・footprint・pin mapping監査、ESP32-C3アンテナkeepout、USB差動配線要件、AP63203およびMLCC監査、Phase 3Bの主要部品仮配置である。

未完了のまま終了する項目は、配線、via、GND zone、最終外形、stack-up、USB差動geometry、最終DRC、Gerber、drill、BOM/CPL、製造発注、実機のUSB/SEN66/Wi-Fi/MQTT/電源評価である。現在のPCBは設計途中であり、製造可能・動作保証済みではない。

## 成果物の扱い

既存の`.kicad_pro`、`.kicad_sch`、`.kicad_pcb`、ローカルfootprint、配置用pcbnewスクリプト、監査資料および要件資料は削除しない。すべて**歴史的・参考用の設計資料**であり、製造承認または後継設計への自動適用を意味しない。

後継検討で再利用できるのは、SEN66接続条件、I2C要件、部品監査、アンテナ・電源設計上の知見である。未配線PCB、仮配置座標、未確定stack-up、未確定USB geometryを後継設計へ流用してはならない。

## リリース状態

| 項目 | 最終状態 |
| --- | --- |
| Project status | CANCELLED |
| Manufacturing release | CANCELLED |
| Fabrication permitted | NO |
| Gerber/BOM/CPL generation | NOT APPLICABLE |
| 未解決VERIFY/BLOCKED | 解決済みではなく、計画中止により未完了のまま終了 |
