# RS-WSUHA-P設定処理の根拠と制約

この文書は、Bルート・アプリ Phase 2で自動化するRS-WSUHA-P設定の根拠を記録します。未確認のコマンドや値を推測して実装しないため、確認済み範囲とTODOを分けています。

## 参照したメーカー資料

- [RS-WSUHA-P製品ページ](https://www.ratocsystems.com/products/wisun/usb-wisun/rs-wsuha/)
- [RS-WSUHAスタートガイド Rev.1.1](https://www.ratocsystems.com/pdffile/wisun/wsuha_startguide.pdf)
- [RS-WSUHAハードウェア仕様書 Rev.1.0](https://www.ratocsystems.com/sol/wp-content/uploads/2023/03/wsuha_spec.pdf)
- [RATOC e2e Store技術記事: スマートメータからWi-SUN Bルートで電力量を知る（その1）](https://www.ratoc-e2estore.com/blog/2023/06/wsuha-01)
- [RATOC e2e Store技術記事: 同（その2）](https://www.ratoc-e2estore.com/blog/2023/06/wsuha-02)

製品ページとスタートガイドは、RS-WSUHA-PがBP35C0を搭載し、BP35C0/BP35C2用DSEコマンドリファレンスを使用することを案内しています。正式コマンドリファレンスの取得には、実機の`SKINFO`応答から得る別の認証情報が必要です。このリポジトリにはその認証情報を保存しません。

## 自動設定に使用する確認済みコマンド

Phase 2で`uart_mode`を実装し、Phase 4のECHONET Lite受信に必要な
`output_mode`を追加しています。

```text
RUART
OK 00

WUART 80
OK

RUART
OK 80

ROPT
OK 00

WOPT 01
OK

ROPT
OK 01
```

- `RUART`: 現在の`WUART`設定値を読む
- `WUART 80`: 115200bps、文字間インターバルなし、UARTフロー制御有効の設定を書き込む
- `ROPT`: 現在の受信EDATA表示形式を読む
- `WOPT 01`: ERXUDPのEDATAを16進ASCIIで受信する設定を書き込む
- `80`は2桁の16進値として扱い、独自にビット値を合成しない
- `01`はメーカー公開サンプルが前提とする16進ASCII受信モードであり、独自の値を合成しない

ハードウェア仕様書は、`WUART`の実行ごとに内部FLASHへ書き込まれ、電源再投入後も保持されること、書込み回数に10,000回以下の制限があることを明記しています。このため、実装は必ず次の順序にします。

1. 対応するreadコマンド（`RUART`または`ROPT`）で毎回実機から読む
2. 期待値と一致すれば書き込まない
3. 不一致の場合だけ対応するwriteコマンドを1回送る
4. 対応するreadコマンドで再読出しする
5. 期待値と一致しなければエラーにする

最初の読出しに失敗した場合、状態を推測して`WUART`を送ってはいけません。

## シリアル条件

公開資料で確認できる条件は115200bps、8 data bits、parity none、1 stop bitです。メーカーの公開Python例に合わせ、コマンドはASCII大文字とCRLFで送ります。受信側はCR、LF、CRLFのいずれでも行を分離できるようにします。

ハードウェア仕様書は、USBポートの認識完了から最初のコマンドまで3秒以上待つよう求めています。アダプターの`open()`後にこの待機を行います。

応答待ちは、1回のシリアル読込みタイムアウトだけでなく、`retry.request_timeout_seconds`を要求全体の期限として扱います。未知通知が途切れず届く場合でも期限を延長せず、確認済み形式の応答が得られなければタイムアウトにします。

公開スタートガイドは初期接続時のホスト側フロー制御を`none`とし、公開Python例も`rtscts`を指定していません。一方、`WUART 80`はドングル側のUARTフロー制御を有効にします。Phase 2では公開Python例と同じホスト設定を使用し、次の点をTODOとして残します。

- `WUART 80`適用後にホスト側RTS/CTSの変更またはポート再オープンが必要か
- 設定反映の正確なタイミング
- WindowsとRaspberry Pi OSでの実機差

## 推測実装しない項目

- 正式マニュアルに記載されたエラー応答とエラーコード
- `00`、`80`以外のUART設定値と全ビット定義
- 応答行へ非同期通知が割り込む場合の厳密な順序保証
- 公式VID/PID
- `WOPT 01`以外の出力モード値と全ビット定義

`ROPT`／`WOPT 01`は、メーカー記事と公開サンプルがECHONET Liteの
ERXUDPデータを16進ASCIIとして扱う前提を明記しているため採用しました。
`uart_mode`と同様、毎回`ROPT`で実機値を確認し、不一致時だけ`WOPT 01`を
実行し、再読出しで確認します。
