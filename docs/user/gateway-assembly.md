# 2-2. Gatewayの組み立て

用意したRaspberry Pi 4、Touch Display 2（7インチ）、SmartiPi Touch Pro 3 サイズSを組み立てます。メーカーの手順に沿いながら、OMK用の接続を整える補助ガイドです。

<img src="../images/omk-gateway/omk_overview.jpg" alt="Touch Display 2とケース、Onyxを組み合わせたOMK Gatewayの完成例。画面はセンサ追加後の表示" width="600">

写真はセンサ追加後の完成例です。このページでは組み立てまで行い、OSとDashboardは次のページで設定します。

## この手順の前に

- Raspberry Piの電源とUSB機器をすべて外します。ディスプレイのケーブルやGPIO電源線は、電源を切ってから接続してください。
- ケース・ディスプレイの付属品、ネジに合うドライバー、ポートカバー加工用のカッター、Onyx、利用開始済みのSORACOM Air SIM（nano SIM、plan-D）、USBキーボードを用意します。
- microSDへのOS書き込みは次のページで行います。組み立て中はカードを入れず、カードの差し込み口へ後からアクセスできるようにします。

## 1. 公式手順でケースとディスプレイを組む

[SmartiPi Touch Pro 3公式組み立てガイド](https://smarticase.com/pro3)の、**Raspberry Pi Official Display 2とRaspberry Pi 4**の説明に従います。OMK標準構成ではカメラやHATを追加しません。

1. ディスプレイ側に映像ケーブルと電源ケーブルを取り付けます。Pi 4にはTouch Display 2付属のPi 4用ケーブルを使い、Pi 5用と取り違えないでください。
2. ディスプレイをケース前面へ収め、公式手順のネジと取り回しで固定します。
3. ケース付属のmicroSD延長部品を取り付け、Raspberry Pi 4を固定します。USB端子がケース外から使える通常の取り付け位置にします。
4. ディスプレイの映像ケーブルをPiの**DISPLAY（DSI）端子**へ、電源線をGPIOへ接続します。microSD延長部品をPiのカードスロットへ接続します。

端子の向きは[Raspberry Pi公式のTouch Display 2接続手順](https://www.raspberrypi.com/documentation/accessories/touch-display-2.html)のPi 4向け説明・写真で照合してください。カメラ端子と取り違えず、コネクタのロックを開けてからケーブルを差し、最後にロックを戻します。GPIOの電源線は指定のピン位置・向きに合わせます。

## 2. ケースを閉じて設置する

1. ケーブルがネジ穴や蓋の縁に重ならず、折れ曲がりや引っ張りがないことを確認します。
2. ポートカバーを使う場合は、公式手順に沿ってカッターで使用する端子の部分を切り抜きます。背面カバーと台座を固定します。ケース上部のmicroSD差し込み口と、USB・電源端子を使える状態にします。
3. Raspberry Pi 4用電源を接続できるようにケーブルを配置します。まだコンセントへはつなぎません。画面の横向き表示は次のソフトウェアセットアップで設定します。

## 3. SIMとOnyxを取り付ける

OnyxはUSBへ接続する前に、[SORACOM公式のSIM挿入手順](https://users.soracom.io/ja-jp/guides/usb-dongles/soracom-onyx/hardware/)に従い、本体のイラストとSIMの向きを照合して挿入し、カバーを閉じます。その後、OnyxをRaspberry PiのUSB端子へ接続します。

回転コネクタと3Dプリント固定具を用意した場合は、この段階で取り付けます。取り付け条件は[「OMK Gateway筐体」](../../hardware/enclosures/omk-gateway/README.md)を参照してください。USB端子に力をかけず、Onyxの放熱を妨げないようにします。

## 4. Bルート用アダプタを接続する（利用する場合）

Bルートを使う予定でRS-WSUHA-Pを用意している場合は、GatewayのUSB端子へ接続し、そのまま次のセットアップへ進みます。

Bルートを使わない場合、またはアダプタをまだ用意していない場合は、未接続で進められます。

## 5. ソフトウェア設定を始められる状態にする

- Touch Display 2がケース内に固定され、Piへ映像・電源の両方が接続されている。
- SIMを入れたOnyxがPiのUSB端子へ接続されている。
- USBキーボードをPiへ接続してある（AP切り替え後に使用）。
- microSDを挿せる状態で、Piの電源はまだ入れていない。
- Bルートを使い、RS-WSUHA-Pを用意している場合はUSBへ接続してある。使わない場合や未入手の場合は未接続でよい。

これでGatewayの物理的な組み立てが完了しました。

次へ：[「2-3. Gatewayセットアップ」](gateway-setup.md)
