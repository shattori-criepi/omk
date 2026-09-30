# AtomS3 Lite配布ファームウェアのライセンス監査

監査日: 2026-10-01。対象は `prebuilt/atom-s3-lite/` の3バイナリと、現在の通常Node firmware。M5Stick-C、開発用reset/wifi-setイメージ、Gateway、開発ツール自体の再配布は対象外。

**判定:** 下記の第三者通知をバイナリに伴わせることで、今回確認した構成について公開を妨げる未対応のライセンス条件は認められない。例外なしのGPL/LGPLコードの組込みは検出していない。ただし、libstdc++には **GPL-3.0-or-later WITH GCC-exception-3.1** が適用されるため、「GPLを全く含まない」とは表現しない。

- 配布時に保持する文書: [THIRD_PARTY_NOTICES.md](prebuilt/atom-s3-lite/THIRD_PARTY_NOTICES.md)
- 配布バイナリのSHA-256と、リンクされたアーカイブ・メンバー・ライセンスを記録した [レビュー済みSPDX SBOM](prebuilt/atom-s3-lite/sbom.spdx.json)
- [OMK独自のLICENSE](../../../LICENSE) は第三者許諾の代用ではない。今回変更していない。

## 配布物との対応

現在の配布物は、clean HEAD `c375ca9aff5768ee97f4b9babdbb01c0ccc2bfbe` から生成されたpackageを対象とする。`scripts/build-omk-node-package.sh` はbuild前にPlatformIO cleanを実行する。manifestの `source_commit` はこのHEADと一致し、配布appのversion文字列は `c375ca9`、`-dirty` は含まれない。

| 配布物 | bytes | 配布済みSHA-256 |
| --- | ---: | --- |
| bootloader.bin | 21088 | `52866528078138249ca6bd9d040638123665a4f933a1c3b1015991c5b223f695` |
| firmware.bin | 1546336 | `c6c31ae7873e2fb935212a414577a4258c08a49f4a872561d0123c861d4c7b9a` |
| partitions.bin | 3072 | `417facb149358cb4532fd2368e67453fc961890f1a56d6145092619a55fc365d` |

現在の `.pio/build/atom-s3-lite/` の3binと `prebuilt/atom-s3-lite/` の3binは、メタデータ・checksumを含め全byte一致する。各SHA-256はmanifestおよびレビュー済みSBOMのfile checksumとも一致し、サイズはmanifestと一致する。今回のmapとSBOMはこの配布物に直接対応する。独立した再buildによる再現性試験を意味するものではない。

今回の更新では既存のclean build成果を使用し、build/clean/package生成は実行していない。prebuilt binary、manifest、`THIRD_PARTY_NOTICES.md` は変更していない。前回監査からfirmware source・依存lock・PlatformIO設定・sdkconfig・partition CSVに変更はなく、リンク対象アーカイブと残存member集合、第三者ライセンス情報にも差はない。

## 実施方法とツールの限界

1. 初回監査ではclean buildを実施。今回の更新では、上記HEADから作成済みの `.pio/build/atom-s3-lite/` と配布物の全byte一致を確認して、そのmap/ELF/component情報を使用した。
2. `esp-idf-sbom==1.4.0` を使用し、現在のappとbootloaderの `project_description.json` からそれぞれ `--rem-unused --rem-config --file-tags` でSPDXを再生成した。前回保存した公式出力と比較し、第三者packageの版・license・copyright情報に差がないことを確認した。
3. `omk_node.map` と `bootloader/bootloader.map` の `Linker script and memory map` 部分を確認。discarded sections、単なる `LOAD`、cross-referenceを組込みの証拠にせず、非zero address/sizeの入力sectionを残すアーカイブを抽出した。appは70アーカイブ、bootloaderは10アーカイブ。全アーカイブ名、SHA-256、残存object名をレビュー済みSBOMの `sourceInfo` に保存した。
4. 初回監査でコンポーネントのsourceリスト、残存object、ライセンス原文を照合し、GCC `-M` で617コマンド・1926 source/header依存を確認した。SPDXタグの後に残る原著者の許諾（TinyCrypt等）も確認済み。今回はsource/依存設定、残存member集合、公式SBOMの第三者情報に変更がないことを確認し、このライセンス判定を維持した。
5. PlatformIOのpackage情報、`dependencies.lock`、IDFのsubmodule/`sbom.yml`、toolchainのランタイム原文を照合した。
6. 元の公式SBOM出力をそのまま配布せず、下記の誤検出・不足を補正したSBOMを作成した。今回の原出力はローカルの `.pio/license-audit/app-c375ca9-esp-idf-sbom.spdx.json` と `bootloader-c375ca9-esp-idf-sbom.spdx.json` に保存（Git対象外）。

公式ツールだけでは今回の結論を得られなかった。

- PlatformIOのtoolchainを識別できず、ランタイムをSBOMに含めない警告が出る。mapからPicolibc `libc.a` とGNU `libstdc++.a` を追加確認した。package全体のGPL表記をランタイムに一律適用していない。
- コンポーネント内部の未使用サブディレクトリやtestまで走査する。Bluedroid構成なのにNimBLEが出現し、実際にリンクされないCC0/Unlicense等のtest由来タグも集計される。
- 逆にcJSON、HTTP Parser、lwIP、Picolibc等が `NOASSERTION` になる。TinyCryptの先頭Apacheタグだけでは、同じファイルのIntel/Kenneth MacKayのBSD許諾を拾えない。
- OMKプロジェクト自体にApache-2.0を推定する。レビュー済みSBOMではOMKの許諾を `NOASSERTION` とし、独自LICENSEへの説明を記載した。
- metadataのライセンス集計は最終バイナリのsection単位の判定ではない。SBOMのコンポーネント版はIDF 6.0.1同梱版を意味し、独立した上流最新版を意味しない。

ツールの機能と制約の根拠: [Espressif esp-idf-sbom](https://github.com/espressif/esp-idf-sbom)、[ESP-IDF copyright documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/COPYRIGHT.html)。適用条件の判定には、ウェブ上の最新版よりも**今回インストールされ、リンクされた版の原文**を優先した。

## 検出したライセンスと義務

表中の著作権者は要約。年・原文・その他のheader著作権表示は配布通知に保持した。すべての行で、今回のバイナリ再配布にOMKまたは第三者のソースコード提供義務は認められない。GCC行は例外の適用を前提とする。

| ソフトウェア／組込み先 | 選択したライセンス | 著作権者（要約） | バイナリ再配布時の対応 |
| --- | --- | --- | --- |
| ESP-IDF 6.0.1 core、bootloader、drivers、Espressifの変更部分 | Apache-2.0 | Espressif Systems | Apache本文を同梱。適用するNOTICEがあれば保持。第三者sourceを変更して配布する場合は変更表示・既存表示の保持 |
| Wi-Fi/Mesh `libcore/libespnow/libmesh/libnet80211/libpp`、PHY `libphy/libbtbb`、coexistence `libcoexist`、BT controller `libbtdm_app` | Apache-2.0 | Espressif Systems | 各バイナリlibディレクトリのLICENSEを確認。同一Apache本文を1回同梱。ソース非公開のlibであること自体は再配布禁止を意味しない |
| Bluedroid host / OS adaptation | Apache-2.0 | Broadcom、Google、Espressif | 同上 |
| ESP-MQTT 1.1.0 | Apache-2.0、BSD-3-Clause (`mqtt_msg.c`) | Espressif、Stephen Robinson | Apache本文＋BSDの著作権・条件・免責文を同梱 |
| cJSON 1.7.19（registry wrapper 1.7.19~2） | MIT | Dave Gamble and cJSON contributors | 著作権表示と許諾・免責文を同梱 |
| FreeRTOS 10.5.1、Xtensa port | MIT、Espressif変更部分はApache-2.0 | Amazon、Cadence、Espressif | MIT通知＋Apache本文。古いFreeRTOS版のGPL条件を当てはめない |
| Xtensa HAL / assembly / headers | MIT、Espressif変更部分はApache-2.0 | Tensilica、Cadence、Espressif | MIT通知＋Apache本文。チップ内ROMを呼ぶ部分と、実際にコピーされるHAL/portを区別 |
| lwIP 2.2.0、ESP netif、TCP ISN等のhooks | BSD-3-Clause、Espressif変更部分はApache-2.0 | Swedish Institute of Computer Science、Adam Dunkels、Leon Woestenberg、Axon、CITEL、Inico、MINIX等 | 著作権・BSD条件・免責文を同梱。著作者名を無断で推奨・宣伝に利用しない |
| wpa_supplicant 2.10 | BSD-3-Clause、Espressif変更部分はApache-2.0 | Jouni Malinen and contributors、Cozybit、Linux Foundation、Espressif | 同上。歴史的BSD/GPL選択表記はBSDを選択。`COPYING` は2012-02-11以降BSDのみと説明 |
| TLSF allocator | BSD-3-Clause | Matthew Conte | 著作権・BSD条件・免責文を同梱 |
| TinyCrypt（BT内）、micro-ecc由来ECC | BSD-3-Clause、BSD-2-Clause、Espressif変更部分はApache-2.0 | Intel、Kenneth MacKay、Espressif | 両BSD原文と著作権を同梱。bootloaderの別micro-ecc archiveは未使用でもBT側ECCは組込みあり |
| HTTP Parser 2.7.0 | MIT | Igor Sysoev、Joyent and other Node contributors | 著作権表示と許諾・免責文を同梱 |
| Mbed TLS 4.0.0 / TF-PSA-Crypto 1.0.0 | **Apache-2.0を選択**（原文はApache-2.0 OR GPL-2.0-or-later） | Mbed TLS Contributors、ARM、Espressif | Apache本文を同梱し選択を明記。GPL側によるソース提供義務は発生しない |
| UBSan support (`esp_system/ubsan.c`) | BSD-2-Clause | Linaro、Jiří Zárevúcky、Espressif | 著作権・条件・免責文を同梱 |
| FreeBSD由来endian / libc headers | BSD-2-Clause系（一部原タグ `BSD-2-Clause-FreeBSD`）、BSD-3-Clause、Espressif変更部分はApache-2.0 | Thomas Moestl、Mike Barcroft、UC Regents、Francesco Giancane等 | 当該headerの著作権・許諾・免責文を保持。タグの別名から本文を省略しない |
| Picolibc 1.8.10 / Newlib由来C runtime（47残存members） | BSD-3-Clause、BSD-2-Clause、個別permissive notices（CodeSourcery、Cygnus、Red Hat、SunPro、DJ Delorie、OAR等） | Keith Packard、UC Regents、Red Hat、Arm、Cygnus、Joerg Wunsch、Michael Stumpf、Dmitry Xmelkov、Alexander Popov、Helmut Wallner、Jeff Johnston、Sichen Zhao等 | 使用objectに対応する条件と著作権を同梱。**Cygnus Supportで開発された旨の謝辞**を追加。headerの個別許諾も保持。無関係な他CPU、test、build scriptの条件は除外 |
| Picolibc内Ryu | **Apache-2.0を選択**（Apache-2.0 OR BSL-1.0） | Ulf Adams | Apache本文と著作権・選択を明記 |
| GNU libstdc++ / libsupc++ 15.2.0、GCC runtime headers | **GPL-3.0-or-later WITH GCC-exception-3.1** | Free Software Foundation | GPL本文と例外文を保持。今回の通常GCCによるEligible Compilationの結合Target Codeは独自条件で配布可能。OMKソース公開・relocation object提供を要求しない |

Apacheで独立した追加NOTICEが必要な対象は、今回リンクされた配布元ディレクトリでは見つからなかった。NimBLE/OpenThread、MQTTのPaho test toolのNOTICEは未組込みであり同梱しない。MIT/BSD等は、ファイル名が `NOTICE` でなくても原文上の著作権・許諾・免責文を保持する必要がある。

GCCの根拠: [libstdc++ license](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html)、[GCC Runtime Library Exception FAQ](https://www.gnu.org/licenses/gcc-exception-3.1-faq.en.html)。コンパイラ本体を配布しているわけではなく、通常のGCCコンパイル結果とランタイムの結合物である。非GPL互換plugin等による中間表現の加工は今回のbuild flagsにはない。`libgcc.a` はmapにarchive extraction/cross referenceとして現れるが、今回のappの出力sectionには残らずROMシンボルへ解決される。対して `libstdc++.a` の16 membersは残るため、GPL runtime例外の検討を省略しなかった。

## 組込みと区別したもの

- **bootloader:** `bootloader_support`、`efuse`、`esp_bootloader_format`、`esp_hal_security`、`esp_hal_uart`、`esp_hw_support`、`esp_rom`、`hal`、`log`、`main` の10アーカイブ。今回 `micro-ecc` はコンパイルされるが出力sectionには残らない。ROM関数の呼出先にあるNewlib等は、このbinにコピーされるコードとは区別した。
- **partitions.bin:** `partitions.csv` から生成するpartition情報とチェックサム・padding。実行コード、ライブラリobjectは含まない。生成ツールのライセンスを生成データへ自動的に適用しない。
- **PlatformIO espressif32 7.0.1 / Core、CMake、Ninja、esptool、GCC等のホスト実行体:** ビルド入力の来歴として確認したが、これらの実行体は3binに含まれない。packageのGPL表記だけをfirmwareへのcopyleft義務としない。
- **未使用:** NimBLE、OpenThread、mbedTLS/test、Paho MQTT test tools、Unity/CMock等。IDF全体やcomponent内testのCC0/Unlicense表記は最終binaryの義務を示さない。
- **headerでのみ検出:** `fastpbkdf2.h` のCC0-1.0（対応実装objectは残らない）、GCC PSTL宣言headerの `Apache-2.0 WITH LLVM-exception`（並列アルゴリズムの実装object・実体化は見つからない）。これらを独立したバイナリライブラリとして数えない。LLVM例外にもコンパイル出力に組み込まれた部分の表示免除がある。
- **Picolibc配布全体のGPL/AGPL:** `COPYING.picolibc` にはbuild script等のGPL/AGPL条件もある。使用した47membersを個別のFilesエントリへ照合し、これらの対象ではないことを確認。IDF側の古いコピーではなく、実際のcompiler packageのcopyright文書を使用した。

## 修正内容と再配布手順

初回監査前には、バイナリに必要な第三者のApache全文、MIT/BSD通知、個別C runtime通知・謝辞がなく、ルートのOMK独自LICENSEのみでは条件を満たせなかった。初回監査で配布通知を1文書に集約し、レビュー済みSBOM、監査記録、READMEの導線を追加した。今回はclean package更新に合わせ、SBOMの来歴とhash、およびこの監査記録のみを更新した。ライセンス一覧・リンク構成・再配布義務に変更はなく、第三者通知は維持した。

GitHub checkout/ソースarchiveには通知が含まれる。バイナリを別途zip、Release添付、ミラー、製品同梱資料として配る場合も **`THIRD_PARTY_NOTICES.md` を必ず同梱する**。SBOMの同梱は監査の追跡に有用だが、SBOMだけでは許諾本文の代わりにならない。現在のpackage scriptはclean→build後に既存prebuiltディレクトリ内のbinary/manifestのみを置換し、同ディレクトリの通知を消さない。SBOMは自動更新しないため、package更新時に現在の成果から再生成・照合する。

依存、sdkconfig、toolchain、対象boardまたはsourceを変更したら、この監査結果を自動的に流用せず再確認する。新しいbinaryのSBOMハッシュも更新する。手順は次のとおり（repo rootから、既存のPlatformIO環境を使用）。

```bash
pio run -d firmware/esp32/omk-node -e atom-s3-lite -t clean
pio run -d firmware/esp32/omk-node -e atom-s3-lite
python3 -m venv /tmp/omk-sbom-venv
/tmp/omk-sbom-venv/bin/pip install esp-idf-sbom==1.4.0
/tmp/omk-sbom-venv/bin/esp-idf-sbom create --rem-unused --rem-config --file-tags --no-sync-excluded-cves --format spdx-json@2.2 -o /tmp/omk-app.spdx.json firmware/esp32/omk-node/.pio/build/atom-s3-lite/project_description.json
/tmp/omk-sbom-venv/bin/esp-idf-sbom create --rem-unused --rem-config --file-tags --no-sync-excluded-cves --format spdx-json@2.2 -o /tmp/omk-bootloader.spdx.json firmware/esp32/omk-node/.pio/build/atom-s3-lite/bootloader/project_description.json
```

この生成出力に対して、上記のmap/source/header/runtime原文の照合を再実施する。`sbom.spdx.json` はそのレビュー後の記録であり、公式ツールの出力をコピーするだけでは再生成できない。`.pio/` のmap/ELFや一時venvは一般利用者への配布には不要。

## 検証結果

- 現在のbuild成果と配布物の3binが全byte一致。SHA-256はbinary / manifest / SBOMの3者で一致。manifestのsource commitは上記HEAD、app descriptorのversionは `c375ca9` で、`-dirty` はない。
- app/bootloaderの公式SBOMを再生成。第三者package名・版・license・copyright情報は前回と同一。両mapの70/10アーカイブと残存member集合も同一。
- レビュー済みSBOMの全80アーカイブhashを現在のファイルから照合。更新が必要だったのは、ビルド情報を含む `libesp_app_format.a` と `libesp_bootloader_format.a` の2件。その他78件は前回と一致した。第三者license表現・著作権・依存関係・LicenseRef本文は変更していない。
- SBOMのdocument namespace・作成日時・build来歴、配布appのhash識別子、app/bootloaderのfile checksumと一致判定コメントを更新。partition tableのhashは不変。
- JSON parse、公式esp-idf-sbom parserによる読込、SPDX license式・関係先ID・LicenseRef定義、Markdownのローカルリンク、`git diff --check` を確認。
- 今回は文書とSBOMのみの更新で、clean/buildや実機への書込みは行っていない。初回監査の関連テスト258件成功は履歴として保持し、今回の再実行結果とはしない。package生成のclean→buildを含む既存テスト7件を再実行して成功した。
- 作業開始時とのSHA-256照合でroot LICENSE、第三者通知、既存binary/manifestが不変であることを確認。

## 残る制約

- opaqueなEspressif binary libについて内部実装sourceは取得できない。判断は配布元の当該libに付属するApache LICENSEに依拠する。未確認の内部著作権問題が絶対にないと証明するものではない。
- 新しい依存や別board、将来のRelease添付物には再監査と通知の同梱が必要。この判定は上記3ファイルのハッシュに限定する。
- セキュリティ脆弱性監査・実機動作試験は今回のライセンス監査には含めていない。
