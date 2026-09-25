# data-exporter

既存の`data/processed/`にある日付partition済みParquetを、JSTの日付範囲でCSV/ZIPへストリーミング書き出しする共通engineです。Parquet・JSONLを変更せず、data-transformerも起動しません。

Gatewayへ導入する場合は以下を一度実行します。

```bash
./scripts/setup-data-exporter.sh
omk-export --from 2026-08-01 --to 2026-08-31
```

既定の出力先は`~/omk-exports/`です。`--output-dir /path/to/exports`、`--dataset sen66 --dataset ble_environment`も指定できます。

生成されるZIPは`omk-export_<from>_<to>_<exported-at>.zip`で、対象データがあるdatasetだけのCSVと`manifest.json`を含みます。CSVはUTF-8 BOM付きで、`source_file`と`source_line_number`は含めません。日時はJST offsetを保つISO 8601形式です。

## USBメモリへ安全に書き出す

`setup-data-exporter.sh`導入後、対応USBを1台だけ接続して実行します。対象はFAT32 (vfat) とexFATです。確認したUSBの安定した識別情報をmount前・書き込み前・unmount前にも再確認するため、途中で別のUSBへ交換された場合は書き込みません。未mount時は`/run/omk-export-usb`へmountし、Desktop等がすでにmountしている場合は検出したそのmount pointの`/OMK/`へZIPを生成します。いずれもsync後にdeviceをunmountし、安全に取り外せる状態にします。

```bash
omk-export-usb --status
omk-export-usb --from 2026-08-30 --to 2026-09-01
```

長期間・大容量exportにはexFATを推奨します。FAT32は単一ファイルが約4GiBまでです。USBのformat、partition作成、修復は行いません。

## Dashboardから書き出す

Dashboard管理メニューの「データ書き出し」からも利用できます。GUIはこのserviceと同じengineを使用するため、CSV/ZIP生成ロジックを重複実装していません。Dashboardからの出力先はUSBメモリのみです。

運用上、root helperは固定の`mount`と`unmount`だけを受け付け、呼出し側のUSB identity tokenをhost側で再検出した媒体と照合します。system-managerのmount namespace sandboxと分離したままhost側USBを操作するため、helperはPID 1のmount namespaceへ`nsenter`して`lsblk`、`systemd-mount`、`systemd-umount`を実行します。未mount時の固定mount pointは`/run/omk-export-usb`で、既存automountがあればそのmount pointを利用します。書き出し後はsyncしてunmountし、unmount完了の確認は短時間retryします。
