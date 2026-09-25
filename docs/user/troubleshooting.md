# トラブルシューティング

推奨構成（Raspberry Pi 4、Touch Display 2、SORACOM Onyx）での対処を症状別に示します。解決したら、各項目のリンク先へ戻ってください。

## SSH接続できない

### Windowsからホスト名で接続できない

初期セットアップ中は、WindowsのWi-Fi接続先をImagerでGatewayに設定したSSIDに合わせます。OMK APへの切り替え後は、[AP切替後の操作方法](gateway-setup.md#ap切替後の操作方法)を使います。

`<ホスト名>.local`で接続できない場合は、IPアドレスを指定します。Touch Display 2とUSBキーボードでGatewayのターミナルを開き、次を実行してください。

```bash
hostname -I
```

複数のアドレスが表示される場合は、次のコマンドで既存Wi-FiのIPアドレスを調べます。`inet`の後に表示されるアドレスを使い、末尾の`/24`などは含めません。

```bash
ip -4 addr show wlan0
```

Windows PowerShellで、`<IPアドレス>`を調べた値に置き換えて接続します。

```powershell
ssh omkdev@<IPアドレス>
```

IPアドレスが表示されない場合は、Gatewayのデスクトップのネットワークメニューから既存Wi-Fiを選び、Wi-Fiのパスワードを入力して接続します。その後、もう一度IPアドレスを調べてください。

接続できたら[Gatewayセットアップ](gateway-setup.md#3-omkを取得する)へ戻ります。Touch Display 2とUSBキーボードがあれば、そのままGatewayのターミナルで手順を続けることもできます。

### OSを書き直した後にホスト鍵の警告が出る

`REMOTE HOST IDENTIFICATION HAS CHANGED!`と表示された場合の対処です。**自分で同じGatewayのOSを書き直した場合に限り**、Windows PowerShellで古い接続先の記録を削除します。`<接続先>`には、警告が出たSSHコマンドで指定したホスト名またはIPアドレスを入れてください。

```powershell
ssh-keygen -R <接続先>
```

[GatewayセットアップのSSH接続](gateway-setup.md#2-gatewayへssh接続する)に戻り、接続し直します。OSを書き直していない場合や接続先が同じGatewayか分からない場合は、記録を削除せず、接続先のIPアドレスを上の手順で調べ直してください。

## sudoのパスワード入力が分からない

セットアップ中にパスワードを求められたら、Imagerで設定した`omkdev`のパスワードを入力し、Enterを押します。入力中に文字や`*`が表示されないのは正常です。そのまま実行中の手順を続けてください。

## Gatewayセットアップが途中で停止した

次のメッセージは、再起動または再ログインを待つ正常な停止です。

```text
A reboot is required. Reboot, reconnect, then rerun this command without --with-base to resume.
```

この場合はGatewayを再起動して接続し直します。

```text
Docker group membership needs a new login session. Re-login, then rerun this command without --with-base to resume.
```

この場合はSSHを切断して接続し直します。直接操作している場合はデスクトップからログアウトし、ログインし直します。

操作と再開コマンドは[Gatewayセットアップの再起動・再ログイン](gateway-setup.md#5-基本設定後にセットアップを再開する)に従ってください。**再開時は`--with-base`を外します。**

## パッケージやOMKのダウンロードが失敗する

- **OMK APへの切り替え前**：Gatewayのデスクトップで既存Wi-Fiへ接続します。同じWi-Fiに接続したWindows PCでもWebページが開けなければ、インターネットを使える別のWi-FiへPCとGatewayを接続します。
- **OMK APへの切り替え後**：[Onyxの通信を復旧する手順](#onyxで外部通信できない)へ進みます。Onyxを使わない構成では、インターネットに接続できる有線LANなどをGatewayへ接続してください。

通信が戻ったら、中断していた[Gatewayの初回構築](gateway-setup.md)または[更新](gateway-maintenance.md#omkを更新する)の手順へ戻ります。

## OMK AP・Dashboardを利用できない

### OMK APへの切り替えでSSHが切れた

既存Wi-Fi経由のSSHが切れるのは正常です。Gatewayのセットアップは切断後も進みます。手順7以降の追加設定は、[AP切替後の操作方法](gateway-setup.md#ap切替後の操作方法)でNapter接続かGatewayの直接操作を選んで続けます。

### OMK APがWi-Fi一覧に見えない

1. Gatewayの電源ケーブルを接続し、起動を待ちます。PCやスマートフォンをGatewayの近くへ移動し、Wi-Fi一覧を開き直してください。
2. Touch Display 2でDashboardが開ける場合は、「管理メニュー → OMKアクセスポイント参照」に表示されるSSIDを探します。
3. 初回セットアップでAP切り替えの確認がまだ表示されている場合は、[OMK APへの切り替え](gateway-setup.md#6-omk-apへの切り替え)を完了します。設定済みなら、Dashboardの「管理メニュー → システム操作 → 再起動」を実行し、起動後にWi-Fi一覧を開き直します。

SSIDが見つかったら、次の接続手順へ進みます。

### OMK APへ接続できない・パスワードが分からない

Touch Display 2で「管理メニュー → OMKアクセスポイント参照」を開き、「表示」を押します。PCやスマートフォンで、画面と同じSSIDを選び、表示されたWi-Fiパスワードを入力してください。これは`omkdev`のログインパスワードとは別です。

接続できたら、下のDashboardのURLを開きます。**Wi-FiパスワードやQRコードを第三者へ不用意に共有しないでください。**

### PCからDashboardを開けない

WindowsのWi-Fi接続先が、Gatewayの`OMK-XXXXXX`形式のSSIDになっているかを見ます。異なるWi-Fiに接続していたらOMK APへ切り替え、ブラウザのアドレス欄へ次を入力します。

```text
http://192.168.50.1:8000/
```

`https://`では開けません。Windowsの「インターネットなし」は正常です。開けたら[Dashboardの使い方](dashboard.md)へ戻ります。

### Touch Display 2にDashboardが表示されない

起動後に少し待っても表示されない場合は、GatewayのターミナルまたはSSH接続先で次を実行し、再起動します。

```bash
sudo reboot
```

表示されたら[セットアップ完了の確認](gateway-setup.md#9-セットアップ完了を確認する)へ戻ります。表示されない場合は、末尾の[技術的な診断](#技術的な診断が必要な場合)へ進んでください。

### Dashboardにセンサが表示されない

「管理メニュー → 機器管理」を開きます。

- BLEセンサが「登録済みセンサ」にない場合は、[BLEセンサの登録](dashboard.md#bleセンサを探索して登録する)へ進みます。登録済みでも無効になっている場合は、「編集」で有効にします。
- SEN66を接続したNodeに「Logical IDを登録」が表示されている場合は、[SEN66の登録](esp32-node-setup.md#3-sen66を確認して登録する)を完了します。
- 機器管理に計測値が表示されるのに表示画面に出ない場合は、「管理メニュー → 表示設定」で「おすすめ」を選ぶか、「カスタム」の表示項目に追加します。

登録前のセンサは表示されません。登録と表示設定ができたら、[Dashboardの表示画面](dashboard.md#表示画面)へ戻ります。

## センサや機器の値が更新されない

「管理メニュー → 機器管理」で対象機器の表示に合わせて対処します。

| 症状 | 対処と復帰の目安 |
| --- | --- |
| OMK Nodeがオンラインにならない | NodeのUSB電源とケーブルを接続し直し、Gatewayの近くで給電します。中継用のNodeも電源を入れます。「オンライン」に戻ったら、[設置手順](esp32-node-setup.md#4-設置して正常動作を確認する)へ戻ります。 |
| SEN66が「未検出」のまま | Nodeの電源を外し、[組み立て手順](sen66-node-assembly.md)の配線写真に合わせて接続し直します。給電後に「SEN66（検出済み）」となったら登録へ進みます。SEN66を接続していない中継用Nodeの「未検出」は正常です。 |
| BLEセンサが探索候補に出ない | 機種を[対応機器一覧](supported-devices.md#switchbot対応機器)と照合します。電源・電池を入れ直し、Gatewayの近くへ置いて「センサを追加」で再検索します。CO₂センサはしばらく待って再検索してください。 |
| 登録済みBLEセンサの「最終受信」が更新されない | 電源・電池を入れ直し、Gatewayの近くへ移動します。「最終受信」が更新される場所へ設置してください。Nodeで中継する場合は、そのNodeが「オンライン」になっていることも機器管理で確かめます。 |

検出できたセンサは[Dashboardの機器管理](dashboard.md#管理メニュー)で登録します。登録済みの場合は表示画面へ戻り、計測値の更新を見ます。

## Nodeセットアップを診断する

Dashboardのセットアップ画面に表示される症状から進んでください。**セットアップ中はUSB接続と給電を維持します。**

| 症状・表示 | 対処 |
| --- | --- |
| USB接続したAtomS3 Liteが候補に出ない・「USB機器確認」で失敗する | 処理開始前か「セットアップ失敗」の表示後に、対象のAtomS3 Liteを1台だけGatewayへ直接接続します。データ通信対応のUSBケーブルへ交換し、機器管理を開き直します。 |
| 「firmware package確認」で失敗する | [Gatewayの更新手順](gateway-maintenance.md#omkを更新する)を実行してから、Nodeのセットアップをやり直します。 |
| 「Wi-Fi設定」や「OMK接続確認」で失敗する | NodeをGatewayの近くへ置き、PCのWi-Fi一覧にOMK APのSSIDが表示されるかを見ます。表示されなければ[APが見えない場合](#omk-apがwi-fi一覧に見えない)へ進みます。 |
| 使用済みNodeのセットアップが止まり、再セットアップを案内される | [既存Nodeを使い直す手順](esp32-node-setup.md#既存nodeを再セットアップする)へ進みます。 |

対処後は[Nodeのセットアップ](esp32-node-setup.md#2-dashboardからセットアップする)へ戻ります。書込み中の失敗や、上記で解決しない場合は、末尾の[技術的な診断](#技術的な診断が必要な場合)へ進んでください。通常の更新は[Nodeの更新手順](esp32-node-setup.md#firmwareを更新する)を使います。

## Bルートのデータを取得できない

「管理メニュー → Bルート設定」を開き、表示に合わせて対処します。

| 表示・症状 | 対処 |
| --- | --- |
| アダプターが接続されていない | RS-WSUHA-PをGatewayへ接続します。初回設定が未完了なら、[Bルートのセットアップ](gateway-setup.md#8-bルートを使用する場合は設定する)を実行します。 |
| ID・パスワードが「未設定」、または「認証エラー」 | 電力会社から発行されたBルートIDとパスワードを入力し直し、「保存して接続」を押します。 |
| 「スマートメータ未検出」・「再試行待ち」 | BルートID・パスワードを発行時の情報と照合し、Gatewayをスマートメーターに近い場所へ移動します。「今すぐ再試行」が表示されていれば押します。 |
| アダプターが応答しない | RS-WSUHA-Pだけを抜き、数秒待って同じUSBポートへ接続し直します。 |
| 接続してもアダプター未接続の表示が続く・「サービス停止」 | [Bルートの再設定](gateway-maintenance.md#bルートを使う)を実行します。 |

「接続済み」になったら、[Dashboardの表示画面](dashboard.md#表示画面)で電力データを確認します。

## SORACOM Onyx / Napterを利用できない

### Onyxで外部通信できない

1. OnyxをGatewayのUSBポートへ接続し直します。
2. SORACOMユーザーコンソールのSIM管理で、使用中のSIMが利用開始済みで、休止・利用中断になっていないかを見ます。未開始・休止中なら利用を開始・再開します。
3. SIMを挿し直す場合は、OnyxをGatewayから外し、本体の表示に合わせてSIMを挿入してから接続します。
4. 接続が戻らない場合は、[Onyxの追加・再設定](gateway-maintenance.md#soracom-onyxを追加再設定する)のセットアップを実行します。plan-DUのSIMでは、同じ節にあるAPN指定を付けてください。

SIM管理で「オンライン」になったら、失敗したダウンロードやNapter接続を再試行します。Gatewayの初回構築中にセットアップが止まっていた場合は、[再開コマンド](gateway-setup.md#再接続後にセットアップを再開する)で続けてください。

SIMやOnyxの識別番号（ICCID・IMSI・IMEI）は、公開するログ・画面写真やGitHubのIssueへ載せないでください。

### NapterでSSH接続できない

1. PCをインターネットに接続できるWi-Fiなどへ接続します。OMK APはPCへインターネット接続を提供しません。
2. SORACOMユーザーコンソールで対象SIMが「オンライン」かを見ます。オフラインなら、上のOnyxの対処を行います。
3. Napterの接続情報が期限切れなら、オンデマンドリモートアクセスを作成し直します。PCからのSSHには現在表示されている接続先とポートを使い、ユーザー名を`omkdev`にします。パスワードはImagerで設定したものです。

具体的な画面操作は[SORACOM公式のSSH接続手順](https://users.soracom.io/ja-jp/docs/napter/login-with-ssh/)を参照してください。接続できたら、中断していたGateway上の作業へ戻ります。

## USBメモリへデータを書き出せない

「管理メニュー → データ書き出し」を開き、画面の表示に合わせて対処します。

### USBメモリを認識しない・書き出しボタンを押せない

| 表示・症状 | 対処 |
| --- | --- |
| 「USBメモリが見つかりません」 | USBメモリをGatewayへ挿し直します。認識しなければ別のUSBメモリを使います。 |
| 「USBメモリを1つだけ接続してください」 | 書き出し中でないことを確かめ、対象以外のUSBメモリを取り外します。 |
| 「FAT32 または exFAT のUSBメモリを接続してください」 | FAT32またはexFATのUSBメモリに交換します。 |
| 「USBメモリを確認できません」と表示される | 別のUSBメモリに交換します。 |
| USBは「利用可能」だがボタンを押せない | 書き出すデータを1項目以上選びます。 |

ボタンを押せるようになったら、[データ書き出しの手順](dashboard.md#データを書き出す)へ戻ります。

### 書き出しが失敗する・必要なデータが含まれない

- 「書き出しに失敗しました」と表示されたら、対象期間を短くし、データの種類を減らして再試行します。長期間・大容量の場合はexFATのUSBメモリを使います。
- 空き容量を調べる場合は、下の手順で安全に取り外してからWindows PCへ接続し、エクスプローラーの「PC」でUSBメモリの空き容量を見ます。不足していれば、空き容量の多い別のUSBメモリを使ってください。
- 必要なデータがない場合は、計測した日付が開始日・終了日の範囲に入っているか、対象データにチェックが入っているかを見ます。直近の計測分は書き出せる状態になるまで時間がかかるため、時間を置いて再度書き出します。

### 「USBメモリを安全に取り外せません」と表示される

**この表示が出ている間はUSBメモリを抜かないでください。** 次の方法で取り外します。

1. USBメモリを接続したまま、「管理メニュー → システム操作 → シャットダウン」を選んで実行します。Gatewayの計測も停止します。
2. Gatewayのシャットダウン完了を待ってから、USBメモリを取り外します。
3. Gatewayの電源を入れ直し、[データ書き出しの手順](dashboard.md#データを書き出す)で再度書き出します。

ほかの書き出しエラーの後にUSBを交換する場合も、「USBメモリを取り外せます」が表示されていなければ、この方法で停止してから取り外します。シャットダウンを実行できない場合は、USBを接続したまま[技術的な診断](#技術的な診断が必要な場合)へ進んでください。

## microSD交換後にGatewayを再構築する

新しいmicroSDで[Gatewayセットアップ](gateway-setup.md)を完了します。既存Nodeは[新しいGatewayで使い直す手順](esp32-node-setup.md#既存nodeを再セットアップする)に従い、再接続または再セットアップします。BLEセンサは[Dashboardで登録し直します](dashboard.md#bleセンサを探索して登録する)。完了後はDashboardの表示画面に計測値が表示されることを確認します。

## 技術的な診断が必要な場合

このページの対処で解決しない場合の、開発・保守向けの参照先です。

| 対象 | 診断・実装の文書 |
| --- | --- |
| Gateway・Dashboard | [開発ガイドのGateway確認](../developer/development.md#gatewayでの確認)、[Dashboard README](../../services/dashboard/README.md) |
| OMK Node | [ファームウェアREADME](../../firmware/esp32/omk-node/README.md#troubleshooting) |
| BLEセンサ | [BLE Sensor Manager README](../../services/ble-sensor-manager/README.md) |
| Bルート | [実機通信のトラブルシュート](../../services/broute-meter/README.md#実機通信のトラブルシュート) |
| USB書き出し | [data-exporter README](../../services/data-exporter/README.md)、[System Manager README](../../services/system-manager/README.md) |
