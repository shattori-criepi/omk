# 2-1. OMKのパーツ構成

GatewayとSEN66 Nodeの製作に必要な部品・用品をそろえます。掲載している製品・購入先は参考例です。

- **必須**：Gatewayを動かすために必要なものです。センサ・Nodeの表では、その用途を選んだ場合に必要なものを示します。
- **推奨**：OMKで動作確認している構成に含まれるものです。
- **任意**：設置方法や用途に合わせて追加するものです。

初回は、以下のGateway構成（ケースを含む）と初回セットアップ用品、[SEN66 Node 1台分](#sen66-nodeを使う場合)、[工具・作業用品](#工具作業用品)をそろえてください。Bルートを使う予定でアダプタを用意している場合は、Gatewayの組み立て時に接続します。

## Gateway構成

OMKで動作確認している推奨構成は、Raspberry Pi 4、Raspberry Pi Touch Display 2（7インチ）、SORACOM Onyxです。

| 区分 | 部品 | 参考製品・実使用例 | 役割・選定条件 |
| --- | --- | --- | --- |
| 必須 | Gateway本体 | [Raspberry Pi 4 Model B / 2GB](https://www.switch-science.com/products/5681) | データの収集・保存とDashboardの表示 |
| 必須 | 電源 | [Raspberry Pi公式ACアダプター](https://www.switch-science.com/products/10259) | Raspberry Pi 4へ安定して給電できるもの |
| 必須 | microSDカード | SanDisk microSDXC 64GB SDSQQNR-064Gなど | OS・OMK・計測データの保存用。64GB以上を目安に、高耐久タイプを選びます。 |
| 推奨 | ディスプレイ | [Raspberry Pi Touch Display 2（7インチ）](https://www.switch-science.com/products/9940) | Gateway本体でDashboardを表示・操作 |
| 推奨 | 外部通信機器 | [SORACOM Onyx LTE USBドングル（SC-QGLC4-C1）](https://soracom.jp/store/7326/) | Gatewayのインターネット接続用 |
| Onyx利用時に必須 | SIM | [SORACOM Air SIMカード plan-D ナノSIM](https://soracom.jp/store/13380/) | 通信契約・通信料金が別途必要 |
| 推奨 | ディスプレイ用ケース | [SmartiPi Touch Pro 3 サイズS](https://smarticase.com/products/smartipi-touch-pro-3) | Raspberry Pi 4とTouch Display 2（7インチ）の両方に対応するもの |

このガイドではケースを含めて組み立てます。

### 初回セットアップに用意するもの

| 機材・環境 | 用途・必要になる場合 |
| --- | --- |
| Windows PC | Raspberry Pi ImagerでmicroSDへOSを書き込み、SSHでGatewayの初期設定を行う |
| microSDカードリーダー | PCにmicroSDカードスロットがない場合 |
| インターネットに接続できる既存Wi-Fi | 初回セットアップ時 |
| USBキーボード | AP切り替え後にGatewayのターミナルでBLE・Bルートサービスを設定する |

### 外部通信

標準構成ではOnyxと利用開始済みのSORACOM Air SIM（plan-D）を使います。事前に[SORACOM公式「利用開始手順」](https://users.soracom.io/ja-jp/guides/getting-started/)を参照し、アカウントとSIMを用意してください。

### Onyxの取り付け用部品（任意）

| 部品 | 参考製品・成果物 | 用途 |
| --- | --- | --- |
| USB回転コネクタ | [エスエスエーサービス USB A変換コネクタ（Aメス/Aオス 回転式）](https://www.amazon.co.jp/dp/B00OPYC7DK) | Onyxの向きを90度変えてGatewayの側面に沿わせる |
| Onyx固定用部品 | [onyx-usb-holder.stl](../../hardware/enclosures/omk-gateway/onyx-usb-holder.stl) | Onyxを固定する部品を3Dプリントする |

## Bルートを使う場合（任意）

| 区分 | 部品・情報 | 参考製品 | 用途 |
| --- | --- | --- | --- |
| 必須 | Wi-SUN USBアダプター | [ラトックシステム RS-WSUHA-P](https://www.ratocsystems.com/products/wisun/usb-wisun/rs-wsuha/) | GatewayへUSB接続し、低圧スマートメーターの電力データを受信 |
| 必須 | Bルートの利用申し込みとID・パスワード | 電力会社から発行される情報 | スマートメーターへの接続用 |

Bルートを使わない場合は、これらの準備は不要です。ID・パスワードが未発行でもGatewayのセットアップは進められます。

## SEN66 Nodeを使う場合

温度、相対湿度、CO₂、PM1.0、PM2.5、PM4.0、PM10、VOC Index、NOx Indexを計測する構成です。

| 区分 | 部品 | 参考製品・実使用例 | 用途・選定条件 |
| --- | --- | --- | --- |
| 必須 | 空気質センサ | [Sensirion SEN66](https://www.digikey.jp/ja/products/detail/sensirion-ag/SEN66-SIN-T/25700945) | 空気環境の計測 |
| 必須 | 接続基板 | [Adafruit SEN6x Breakout for Sensirion SEN66 - STEMMA QT / Qwiic](https://www.digikey.jp/ja/products/detail/adafruit-industries-llc/6331/26761418) | SEN66とAtomS3 Liteの接続 |
| 必須 | 小型端末 | [M5Stack AtomS3 Lite](https://www.switch-science.com/products/8778) | センサの値をGatewayへ送信 |
| 必須 | Grove/Qwiic変換ケーブル | [Adafruit 4-PIN STEMMA/GROVE - QT/QWIIC 4"変換ケーブル](https://www.digikey.jp/ja/products/detail/adafruit-industries-llc/4528/11627737) | 接続基板とAtomS3 Liteを接続 |
| 必須 | SEN66用ケーブル | [Adafruit #5754 JST GH 1.25MM PITCH 6 PIN CABLE](https://www.digikey.jp/ja/products/detail/adafruit-industries-llc/5754/21839797) | 両端のコネクタの表裏の向きが反転したもの。代替品はAdafruit 5754と向き・配線が同じものを選びます。 |
| 必須 | USB-C to USB-Aケーブル | [Anker Zolo USB-C & USB-A ケーブル 1.0m](https://www.ankerjapan.com/products/a8052)など | 初期設定にも使うため、データ通信・給電の両方に対応したもの |
| 必須 | USB電源 | USB-A出力のACアダプター（5V、1A以上を目安） | 設置場所での常時給電 |
| 初回ガイドで使用 | ケース | [ケースのSTLデータ](../../hardware/enclosures/sen66-node/) | ケース本体と蓋を3Dプリントする |
| 初回ガイドで使用 | ネジ | M3×8mmネジ6本 | ケースの蓋を固定する |
| 任意 | ケーブル固定具 | [sen66-node-cable-jig.stl](../../hardware/enclosures/sen66-node/sen66-node-cable-jig.stl) | 適合するUSBケーブルが抜けにくくなる。固定具なしでも利用できます。 |

ケーブル固定具はAnker Zolo USB-C & USB-A ケーブルへの適合を確認しています。別製品はUSB-Cプラグ周辺の形状が合わない場合があります。

## 工具・作業用品

GatewayとSEN66 Nodeの組み立てで使う工具を、作業前に用意してください。

| 工具・作業用品 | 用途・条件 |
| --- | --- |
| ケース組み立て用ドライバー | SmartiPi Touch Pro 3の付属ネジに合うもの |
| カッター | SmartiPi Touch Pro 3 サイズSのポートカバーを、使用する端子に合わせて切り抜く作業に使用 |
| M3タップとタップハンドルなどの加工用工具 | 電子部品を入れる前にSEN66ケースのネジ穴を加工する |
| SEN66ケース用ドライバー | 用意したM3×8mmネジの頭に合うもの。Gateway用と合えば兼用できます。 |

## このページの完了と次の手順

GatewayとSEN66 Node 1台分の部品、初期設定用のPC・ネットワーク・工具を用意できたら準備完了です。

次へ：[「2-2. Gatewayの組み立て」](gateway-assembly.md)
