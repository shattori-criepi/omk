# OMK Nodeを更新・再設定する

導入済みのOMK NodeのLogical ID変更、接続先の再設定、ファームウェア更新を行うときのページです。初回のSEN66 Node導入は[「3-2. SEN66 Nodeをセットアップする」](esp32-node-setup.md)を参照してください。

## 登録済みのLogical IDを変更・解除する

「管理メニュー → 機器管理」のNodeカードで「Logical IDを変更」を押し、新しいIDを入力して「変更内容を登録」を押します。

「Logical ID登録を解除」を押すと、SEN66の登録を解除します。Wi-Fi設定や過去の計測データは残ります。再登録する場合は[「3-2. SEN66 Nodeをセットアップする」](esp32-node-setup.md#3-sen66を確認して登録する)の登録手順を参照してください。

## 既存Nodeを再セットアップする

別のGatewayへ移す場合や、microSD交換後に使い直す場合、接続先のOMK APを設定し直す場合は、この手順を使います。接続先のGatewayでは、[「2-3. Gatewayセットアップ」](gateway-setup.md)を完了しておきます。

Logical IDを登録済みのNodeが以前と同じWi-Fiへ接続できる場合は、再接続時にLogical IDが自動で復元されます。「機器管理」で「オンライン」と以前のLogical IDが表示され、計測値も届いていれば、再セットアップは不要です。

再セットアップすると、Node IDは変わりませんが、**Wi-Fiは接続先Gateway用に設定し直され、以前のLogical IDは解除されます。** SEN66を使う場合は、完了後に登録し直してください。

1. 対象のAtomS3 LiteだけをGatewayへUSB接続します。ほかの同種USB機器は一時的に取り外します。OnyxとRS-WSUHA-Pは接続したままで構いません。
2. Dashboardの「管理メニュー → 機器管理」で、「接続: USB接続」のNodeカードにある「OMK Nodeを再セットアップ」を押します。
3. 確認画面で、Wi-Fiが再設定されLogical IDが消去されることを読み、実行します。
4. USB接続と給電を維持し、「セットアップ完了」と表示されるまで待ちます。
5. SEN66を使う場合は[「3-2. SEN66 Nodeをセットアップする」](esp32-node-setup.md#3-sen66を確認して登録する)の登録手順でLogical IDを登録し、[「3-2. SEN66 Nodeをセットアップする」](esp32-node-setup.md#4-設置して正常動作を確認する)の手順で設置と計測を確認します。組み立て済みケースの蓋は外す必要はありません。センサを接続せず中継だけに使う場合は[「中継専用OMK Nodeをセットアップする」](relay-node-setup.md#4-設置する)の設置・通信確認を行います。

「OMK Nodeの再セットアップを再試行」が表示された場合は、そのボタンから再開します。完了しない場合は[「トラブルシューティング」](troubleshooting.md#nodeセットアップを診断する)を参照してください。

<a id="firmwareを更新する"></a>

## ファームウェアを更新する

現在、Dashboardには設定を保持したまま更新する専用ボタンはありません。「OMK Nodeを再セットアップ」では、上記のとおりWi-Fiの再設定とLogical IDの解除が行われます。

Wi-FiやLogical IDを保持する通常更新には、開発・保守用の実行環境が必要です。更新手順は[「OMK ESP32 Node」](../../firmware/esp32/omk-node/README.md#factory-flashとpartition-table)を参照してください。

更新後は[「3-2. SEN66 Nodeをセットアップする」](esp32-node-setup.md#4-設置して正常動作を確認する)の設置確認に沿って、Nodeが「オンライン」に戻り、同じLogical IDで計測値が表示されることを確認します。接続先を変える再設定は、このページの再セットアップ手順を使います。

配布済みファームウェアに含まれる第三者ソフトウェアの許諾は[「OMK Node firmware — Third-party notices」](../../firmware/esp32/omk-node/prebuilt/atom-s3-lite/THIRD_PARTY_NOTICES.md)を参照してください。バイナリを再配布する場合は同文書を同梱してください。

## 保守完了の確認

対象Nodeが「オンライン」に戻り、SEN66のLogical IDと計測値、または中継するセンサの受信が更新されることを確認します。
