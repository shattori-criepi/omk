# 021ff22基準のリリース候補

2026-11-04（Asia/Tokyo）。対象は通常のAtomS3 Lite / ESP32-S3版。候補ソースは`021ff22ee6a8ac478ec37043330da07b2a9e5616`のまま確定する。ローカルビルド・ホストテストと保存物の照合を経て、保存済みappを使う配布候補と関連メタデータを正式な`prebuilt/atom-s3-lite/`へ配置した。開発ブランチ・保存BIN・試験記録は保持する。この候補はGitHub未反映であり、実機・Gateway操作は行っていない。

## 候補に含める範囲

比較基準のmain `6d19e3c637ac2a141e7883e63c77cac543e0e0fd`は`021ff22`の祖先であり、間にあるコミットは`26b3ac5`と`021ff22`の2件。候補のコード・ビルド設定は`021ff22`のまま保持する。元の開発ブランチと未コミット変更は別worktreeに保持する。

含めるのは、計測と配送の分離、最新1件mailbox、SEN66連番診断、bounded MQTT登録・送信・購読、Probe進捗確認、固定長Mesh TX、backoff、当時の通信復旧と基本診断である。通常データ8 KiB・MQTT全体12 KiB、Mesh TX固定4枠・単発待ち5秒・未送信フレーム期限10秒を使用する。MQTT poll 100msは比較基準mainにも含まれ、新規の設定変更ではない。

`7f97f80`以降のnetif寿命修正、通信障害によるMCU再起動の撤去、追加runtime診断、memory metadata診断、`6811cdd`のP1予約方式、Mesh lifecycle Cと専用bundleツールは含めない。基本のnetwork diagnosticsは既定ONであり、後続のallocation failure callbackを使うmemory metadata診断とは区別する。従来の通信起因MCU再起動と永続budgetは残る。

## 検証したこと

| 対象 | 結果 | 限界 |
| --- | --- | --- |
| 保存済みSEN66-002版のソースarchive | 112ファイルすべてがビルド前の候補入力とSHA-256一致 | 保存された対応関係。今回、実機から現在ELFを取得していない |
| 保存ビルド入力 | platformio.ini、sdkconfig.defaults、sdkconfig.atom-s3-lite、dependencies.lock、partitions.csvが一致 | 過去ビルド全objectの再現性を主張するものではない |
| 実効Kconfig | 保存済みconfig/sdkconfig.jsonと全キー一致 | 新規BINの実機動作確認はしていない |
| RSSI設定 | high=-78、medium=-82、low=-85 dBm、最大Mesh layer=6 | READMEの2026-09 profileとは異なる。cnx_rssi/backoff_rssi=-78は別のruntime設定 |
| MQTT生成ソース | 保存済み生成コピーと新規build-localコピーが全byte一致 | SDKキャッシュを直接変更していない |
| 通常版クリーンビルド | 成功。PlatformIO espressif32 7.0.1、ESP-IDF 6.0.1、ESP-MQTT 1.1.0、Xtensa GCC 15.2.0 | 既存build出力のない別worktreeで実施。実機未適用 |
| firmwareホストテスト | 346 passed、失敗・skipなし | SDK/RF・実機の負荷や資源寿命を保証しない |
| packageスクリプトのホストテスト | 7 passed、失敗・skipなし | fake Git/PIOと合成成果物を使用。実prebuiltを生成・置換していない |
| ビルド時の追跡済み入力 | ビルド完了時にはビルド前112ファイルのハッシュを維持 | その後の変更は公開文書とprebuilt。コード・ビルド設定は維持 |
| 10月28日以前の実機稼働記録 | 10月26～27日の現在view ELF一致127件とLogical ID対応61件を照合 | 直近7日間の再集計ではない。過去ログは通信エラーも含み、無欠測の保証ではない |
| 配布候補メタデータ | 30件の追加ホストテスト成功。実際のGateway package validator、公式SPDX 2.2スキーマ、6ファイルのSBOM checksum、18 package・80 archive記録と配布経路の説明を検査 | USB・実機書込みは行わない。archive hashは照合用再ビルドの値と明示 |
| 正式配置後の最終検証 | firmware 346件・package 7件・正式prebuiltの配布物検証30件が成功、計383件 | ソースと保存appは維持。新たなビルド・実機確認を追加した結果ではない |

今回の新規ビルドのFlash使用量は1,567,065 / 2,097,152 bytes、静的RAMは79,264 / 327,680 bytes。BINのファイル長とは異なる指標である。

ユーザー報告では、ファームウェアを変更していないSEN66-002は直近7日間で再起動0回・60秒超欠測0回。これは運用観測として扱い、今回のローカル検証で再集計した値ではない。予備NodeのC手動試験と約6日半の安定稼働は別の試験版の結果であり、本候補のRoot・中継・センサ付き動作の合格には算入しない。

## 保存版・新規ビルド・公開prebuiltの識別

| 項目 | SEN66-002対応の保存版・今回の配布app | 照合用クリーンビルド | 更新前prebuilt（履歴） |
| --- | --- | --- | --- |
| app version | 26b3ac5 | 021ff22 | 0a386a9 |
| firmware.bin bytes | 1,567,488 | 1,567,488 | 1,550,864 |
| firmware.bin SHA-256 | c8ca0a32045a12b16934c82075fd5522a2353adc13166a2aa5bf6c3650e04781 | b6b2d00b6941edf524c9ed9f919672bcd89474f2646551c11dae0363e8bdc6a4 | dd8b967c94e69e3fa0c0f413c02bd2541009de5e407886ea04c3437315928162 |
| firmware.elf SHA-256 | ed2c443eb6b89a92a9a8edd2282c606dadcd29dc6c35eabae4aa7aa8f700f732 | 1af52a9bd844e619a880d2fe4ae59b6ef487f472b2f7e840ee66f06bb934c67f | ELFは公開packageに含まれない |
| 実機実績 | 過去のSEN66-002稼働記録と対応 | 今回の実機検証なし | 本候補の検証結果を流用しない |

保存版のapp versionは、その時点の埋込みGit識別子である。保存された作業ソースは後にcheckpointした`021ff22`と112ファイル一致するため、version文字列だけでソースを同定しない。

今回と保存版のfirmware.binは82 bytes異なる。差分はapp descriptor内のversion・日時・ELF SHAと、末尾checksum・image hashに限定される。ELFのロード対象8セクションも、app descriptor以外の7セクションはaddress・size・内容が一致した。新規BINの埋込みELF SHAが新規ELFの実SHAと一致することも確認した。ソース・設定・実行コードの対応を確認したが、新規BINそのものを実機検証済みとは呼ばない。

partition tableは3者とも3,072 bytes、SHA-256 `417facb149358cb4532fd2368e67453fc961890f1a56d6145092619a55fc365d`で一致する。新規bootloaderは21,088 bytes、SHA-256 `a4704404e0eacca8623929a6f4738f8dd4722f99b66f6141a8a225f96f2ccde8`で、保存版・既存公開版のbootloaderとは一致しない。bootloader差分の実機検証も今回行っていない。

## 配布候補の構成と一般配布適性

配布appには上表の保存版をそのまま使用する。新規ビルドappを配布appへ自動コピーせず、保存appのversion・ELF SHAも変更しない。bootloaderは既存公開物を再利用する。既存bootloaderと新規ビルドとの差43 bytes、保存版bootloaderとの差38 bytesは、bootloader descriptorのcompile日時と末尾のchecksum/image hashに限られる。bootloaderの実行内容は対応しており、新たなbootloader更新を避ける構成とした。今回この組合せを実機へ書き込んではいない。

| セグメント | offset | bytes | 採用するSHA-256 |
| --- | --- | ---: | --- |
| bootloader.bin（既存公開物） | 0x00000000 | 21,088 | 8fc701ed4b0d2e3ef6373c98d950440aa0eee4d8017ae634935b3e68e9389ce5 |
| partitions.bin（既存公開物・保存版共通） | 0x00008000 | 3,072 | 417facb149358cb4532fd2368e67453fc961890f1a56d6145092619a55fc365d |
| firmware.bin（保存済みapp） | 0x00010000 | 1,567,488 | c8ca0a32045a12b16934c82075fd5522a2353adc13166a2aa5bf6c3650e04781 |

appは全flash dumpではなく、通常appのESP32-S3 imageである。NVS（0x9000～0xefff）とfactory_secret（0xf000～0xffff）の内容はpackageへ含めない。ESP imageのsegment、checksum、付加SHA、partition tableのMD5と配置を検査した。

Node identityは起動時にeFuse MACから導出し、PoP・Gateway credential・Logical IDはNVS等から読む。保存ビルドのcompiler commandに開発用credential書込み・登録解除・C・memory診断のflagはなく、appにも対象Node IDやsen66-002の固定文字列を検出しなかった。runtime設定を含むcode/dataは通常版の照合用ビルドと一致する。app内の`OMK-Test-SSID`と`correct-horse-battery-staple`はMesh導出の公開自己試験vectorで、実運用credentialとして使うものではない。試験専用Node設定や実credentialを組み込んだ理由は見つからず、保存appを汎用配布候補として採用できると判断した。

manifestの`source_commit`は対応ソースを指す`021ff22ee6a8ac478ec37043330da07b2a9e5616`である。保存appの元のビルドをcleanな021ff22から実行したという意味ではない。保存当時のapp version `26b3ac5`、同一ソース112ファイルの照合、実効Kconfig、日時等の82 bytes差、旧公開bootloaderの再利用は`provenance.json`に記録する。元のELFは同定に使用し、debug情報を含むELF自体は一般配布packageに含めない。

packageは3bin、manifest.json、sbom.spdx.json、THIRD_PARTY_NOTICES.md、provenance.jsonの7ファイルとする。実験ログ、source archive、端末固有の接続先、credentialファイルは同梱しない。

Dashboardの初回セットアップは公開prebuiltを読むため、このpackageを配置した後は保存app候補を使用する。一方、既存の通常更新CLI `scripts/flash-omk-node.sh`は保存prebuiltを使わない。ソースから`pio run`してuploadするため、新規ビルドBINとなり、保存appと同じSHAにはならない。今回の82 bytes比較は今回作成した照合用BINとの比較であり、将来の再ビルドの差分数を保証しない。既存Nodeへ保存appそのものを適用するための一般用の更新入口・安全な手順は別途確認が必要で、今回CLIの変更や書込みを行わない。通常更新CLIが保存BINをそのまま配布する、と案内しない。

## 残る実機確認

| 機能 | mainからの動作差分・確認事項 |
| --- | --- |
| 親切替 | cnx_rssi/backoff_rssiを-78に設定・read-backする。通常ビルドで有効。境界RSSIでの再選択、往復切替、配下通信への影響は追加確認が必要 |
| Root再選挙 | Node参加を理由とする再選挙と、強い弱電界が続いた時のRoot親再選択を撤去。弱いuplinkと切断増加を組み合わせるvoteは残る。Root喪失・復帰、役割昇降格、単一Rootの維持、DHCP/NAPT/MQTTを確認する |
| 第3階層中継 | 原因未特定・未解決。SDK内の中継処理を直接修正した実装ではない。L1 Root→L2中継→L3子の双方向通信と長時間観測を別途行う |
| センサ付きの共存 | 選定appと採用bootloaderの構成でSEN66読取り、MQTT送信、BLE relay、通常再接続を確認。QoS 0のenqueue成功・Probe ACKを測定保存成功と混同しない |
| 異常時復旧 | IP待ち、親切断、MQTT停滞、budget、再起動猶予・再armの実機効果はホスト試験だけでは保証しない。稼働Nodeで意図的に障害を誘発しない |
| 長期資源 | runtime heap・stack余裕、断片化、イベント競合と長期通信は追加確認が必要。後続netif修正を含めたものと取り違えない |
| VOC連続性 | 起動時のSEN66 stop→reset→startは残る。更新前後の学習連続性を保証しない。sensor boot_idだけでなくsen66_rc/to、read進行、VOCとreset/start記録を確認する |

過去の3層接続成立や既存Nodeの安定期間を、未解決の第3階層中継問題の修正証拠には使わない。試験時の建物の階数とMesh layerも区別する。

## prebuiltを揃える手順

更新前のmanifestはsource_commit `0a386a9d11a88231c6c07631b5a01e8f50958da4`を指し、3binのサイズ・SHAは実ファイルと一致していた。旧SBOMはc375ca9時点のapp/bootloader checksumを保持していた。原因は、10月1日の`c09764e`以降、10月6日の`ff065df`と`7239c21`がbinary/manifestだけを更新し、SBOM・監査記録を更新しなかったことである。package生成スクリプトにSBOM自動更新はない。

配置済みSBOMは、実際の配布ファイル6件（SBOM自身を除く）のchecksumとapp packageのversionInfoを更新し、古い「現在のclean buildと全byte一致」という記述を撤去した。公式SPDX 2.2 JSONスキーマでは非標準だった旧`documentComment`も標準の`comment`へ修正した。18 packageの第三者version・license式・著作権、6種類の抽出license本文と通知は既存レビューを維持する。保存appと照合用再ビルドのmapの70 archive/member集合、既存レビューとのapp 70・bootloader 10集合はすべて一致した。SBOMのarchive hashは照合用再ビルドの実ファイルから取得した値であり、存在しない過去archiveのhashを推測したものではない。

1. 準備済み7ファイルのhash、原本・ソース・provenance対応、Root・親切替等の残る実機確認範囲をレビューする。今回の作業では実機確認を追加していない。
2. 保存appのSHAを維持して準備済みpackageを`prebuilt/atom-s3-lite/`へ配置した。更新前との差分はfirmware.bin、manifest.json、sbom.spdx.json、provenance.jsonの4ファイル。bootloader.bin、partitions.bin、THIRD_PARTY_NOTICES.mdは全byte同じである。
3. この保存appリリースでは`scripts/build-omk-node-package.sh`を実行しない。同スクリプトは再ビルド品へ置き換えるため、保存app採用方針と異なる。将来コード・設定を変えた版は別候補としてbuild・実機確認・metadata整備を行う。
4. 配置後もmanifest／実ファイル／SBOM／provenanceを相互照合し、既存Gateway validatorでpackageを読めることを確認する。全体消去・再セットアップを通常更新として案内しない。実機適用はこの作業と別に判断する。
5. 公開対象を個別指定してローカルcommitする。manifestのsource_commitが指す021ff22を公開履歴から到達可能に保ち、文書commitのSHAや元の埋込みversionへ安易に書き換えない。GitHubへのpush・PR・mergeは別工程とする。

## 公開するファイルと保全する情報

ソースと文書の候補は`origin/main..021ff22`の66ファイルと公開文書4ファイルを合わせた69ファイルである。配布候補のapp・manifest・SBOM・provenanceの4ファイルを重ね、mainへ反映する予定一覧は73ファイルとなる。新規の文書はこのページ、追加の配布metadataはprovenance.json。README、LICENSE_AUDIT.md、ESP-WIFI-MESH設計資料は既存ファイルを修正する。firmwareのcode・設定・依存lock・MQTT patchと準備スクリプト・対応するホストテストを一緒に扱う。bounded APIは診断OFFでも必要なので、diagnosticsという名前だけでpatchやCMake連携を削除しない。

ローカルのビルド出力、検証JSON・ログ、pytest一時物、source archive、実機trial bundle、送信済みmarker、credential・PoP・Wi-Fi設定・NVS dumpは公開対象へ追加しない。実NodeのMAC・接続先・個別ログをこのページには転記しない。実験記録の原本は従来の保全領域へ残す。

`git add -A`を使わず、公開対象を個別指定し、staged差分と到達する履歴も検査する。試験専用bundle・ログ・markerと公開対象を分離し、実機操作とGitHubへの反映は別工程とする。
