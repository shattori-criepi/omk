# B-route USB recovery の権限境界と identity 追跡

## 対象と identity

管理者が実通信・設定読出しで確認した RS-WSUHA-P の serial を
`/etc/omk/broute-usb-recovery.conf` に登録する。serial は暗号学的認証ではなく、
管理者登録済みの物理個体の識別子である。`0403:6015` は確認済み FT230X transport
の追加 guard であり、RS-WSUHA-P 公式 VID/PID や機種認証として扱わない。
product descriptor では trust を確立しない。

登録済み環境の `run` は起動時に serial を固定した `TrustedUsbPort` を作り、
存在確認と `PySerialTransport.open()` がこの同じ object を参照する。
現在の USB parent を serial から列挙し、canonical sysfs、transport、tty 対応、
character device の major/minor、一意性を確認する。tty がまだない USB parent も
重複検査に含める。通信路を開いた直後にも再検証し、変化した場合は close して
コマンドを送らない。既に開いた fd を使う通信は、その fd の接続先を使い続ける。

設定の `/dev/serial/by-id/...` は推奨するが必須ではない。検証済み tty を指す
by-id を優先し、udev の alias 生成が遅れていれば検証済み canonical tty を使う。
起動時の `/dev/ttyUSB0` が別 FT230X に再割当てされても、その path は採用せず、
trusted serial の現在の tty に追従する。設定ファイルを書き換える必要はない。
候補 0 件や未生成 tty/node は待機対象、複数・矛盾・不明な sysfs 対応・transport
不一致はエラーとして通信開始を拒否する。登録の変更・削除・不正化にも追従せず停止する。

未登録の通常計測・`setup-adapter` は従来どおり利用できる。登録ファイルが存在するが
不正な場合、`run` は無検証の通信へ fallback しない。登録・交換はサービス停止中に
行い、登録後はサービスを起動し直す。起動中の登録変更はサポートしない。

## 実コード上の recovery sequence

| 段階 | identity / port と境界 |
| --- | --- |
| `run` の通常設定読出し | trusted 登録時は上記 resolver で open。最大 6 回の readiness retry |
| close/open → SKRESET → 設定確認 | 同じ runtime object を使用し、再 open 時に現在の trusted tty を検証 |
| logical reset 要求 | application の 20 分 cooldown と既存回数制限。引数なし sudo helper を呼ぶ前にも trust を検証 |
| root logical helper | root-owned identity、一意な USB parent / tty / device を再検証。対象 USB parent だけを unbind/bind |
| logical reset 直前 | root lock 下で対象を再確認。対象が固定 Gateway hub 配下なら短命の VBUS 許可を発行 |
| logical reset 後 | helper は同じ USB parent/serial の復帰を最大 15 回確認。Python は serial から現在の port を最大 15 回再解決 |
| 設定確認不能 / 実行済み reset の失敗 | application の VBUS 1 時間 cooldown を確認し、引数なし VBUS helper を 1 回要求。事前 identity 検証失敗では進まない |
| root VBUS helper | 下記の許可、trusted identity、Pi 4、hub topology を自身で検証して hub 全体を cycle |
| VBUS 後 | runtime identity の再出現を最大 45 秒待機。5 秒 settle 後、毎回再検証して open / 設定読出し |
| scan → PANA → 計測 | 同じ adapter / transport を継続使用。smart meter 0 件・複数件の拒否は従来どおり |
| 計測中の通信障害 | 既存の close/open → 設定確認 → scan/PANA 再接続。毎回同じ trusted identity を再解決。起動時の全 USB escalation sequence を再実行するものではない |

既存の application recovery state は `data/broute-meter/broute-recovery-state.json` に
保存される。これは UI / 再試行のための状態であり、root helper の authorization
には使用しない。

## root VBUS boundary

`cycle-gateway-usb-vbus` 自身が B-route 専用 helper であり、generic な hub 操作を
NOPASSWD 公開しない。固定の root-owned
`/usr/local/lib/omk/reset-rs-wsuha-p-usb` を source し、logical reset と同じ
identity file 検証・USB parent 検証を使う。両 helper とその配置ディレクトリは
setup が root 管理する。両 sudoers command に空引数条件 `""` を付け、helper 側も
引数を拒否する。service ユーザーは serial、device、sysfs、hub を指定できない。
`uhubctl` 自体には NOPASSWD 権限を与えない。

`/run/omk-broute-usb` は root:root 0700、状態ファイルは root:root 0600。
ディレクトリとファイルの type / owner / mode を検査し、symlink、ファイルの
hardlink、不正形式を拒否する。ディレクトリ inode の `flock` で logical reset と
VBUS を排他する。caller の環境変数でこれらの path を変更できない。

logical helper が現在の trusted adapter を固定 hub の canonical sysfs 配下で確認し、
unbind を試みる直前に `pending` を発行する。serial、USB 接続位置、同一 boot の
uptime を記録し、有効期間は **10 分・1 回限り**。VBUS helper は次を要求する。

1. trusted registration が現在も安全で、許可の serial と一致する。
2. Raspberry Pi 4、固定 `1-1` の `2109:3431` USB2・4-port topology と canonical
   sysfs hub が確認できる。
3. trusted USB parent が現在も一意に同じ hub の同じ接続位置にある。または、
   trusted parent が完全に消失し、旧位置にも代替機器・残った bus symlink がない。
   parent のみ残り tty が消えた直前 unbind 状態も許可するが、存在する tty 対応の
   不正・重複は拒否する。
4. root 管理の直近 VBUS attempt から **1 時間以上**経過している。

許可を消費し `last-cycle` を記録してから電源操作する。操作失敗でも許可は再使用できない。
状態は service restart で残り、Gateway reboot で消える。既存 application の永続
cooldown も別途維持する。新しい永続設定は不要。

直接呼出しでも上記条件は必須。登録済み adapter があるだけでは hub を cycle できない。
一方、許可期間内の直接呼出しは条件を満たせば実行できる。root boundary は protocol
の応答停止自体を証明するものではなく、trusted 個体に対する直近の guarded logical
reset と現在の topology に authorization を限定する。service ユーザーは元から
許可された logical reset を要求でき、その後の条件内で VBUS を要求できる。

adapter が logical helper の検証前から消失していて許可がない場合、期限を過ぎた場合、
別機器への置換・重複がある場合は自動 VBUS を拒否する。管理者による現場確認が必要。
無期限に過去の接続実績を信用する fallback は設けない。

Pi 4 の USB 電源は ganged であり、**VBUS は hub 全体に作用する**。
RS-WSUHA-P だけでなく Onyx、キーボード等も一時 disconnect する。
終了・signal 時の power ON は best effort であり、Onyx / NetworkManager /
SORACOM / Napter その他機器の復帰を B-route helper が保証するものではない。

## 更新と実機確認計画（実施者は管理者）

この変更の自動テストは fake sysfs/dev と記録用 serial / power 操作のみを使用する。
以下は実機で別途行う確認計画であり、今回の修正作業では実施していない。

更新時はサービスを停止し、既存 setup で **両 helper、sudoers、application** を更新して
サービスを起動し直す。helper の片方だけの更新はしない。既存の trusted identity と
stable by-id 設定は引き継げる。setup は active なサービスを restart し、inactive なら start
する。更新後に application が新しい process として起動したことを確認する。

1. ローカルで操作・ログ確認できる状態を用意する。Onyx 経由の遠隔接続は VBUS で
   切れる前提とし、確認期間には他 USB 機器への影響を許容する。
2. 通常起動で設定読出し、scan、PANA、瞬時電力・積算電力量、MQTT の復帰を確認する。
   個体情報や認証情報をソース・共有ログへ転記しない。
3. サービスを停止した管理者管理下で、許可が存在しない状態の
   `sudo -n /usr/local/lib/omk/cycle-gateway-usb-vbus` が拒否され、USB disconnect が
   起こらないことを確認する。引数付き呼出しも拒否されることを確認する。
4. 引数なし logical reset を実行し、同一 trusted serial / canonical parent が再出現し、
   stable by-id が正しい tty を指すことを確認する。その後サービスを起動し、
   設定確認 → scan → PANA → 計測 / MQTT が復帰することを確認する。
5. VBUS の正の確認はサービスを停止し、改めて logical reset の直後、cooldown 条件を
   満たした管理下で専用 VBUS helper を実行する。直接呼出しでも有効な root 許可が
   必要であることと、再実行は拒否されることを確認する。必要な再試験は cooldown
   経過後に行い、通常運用で state を削除して制限を迂回しない。
6. VBUS 後、同一 trusted serial / transport / sysfs / tty / by-id を確認してサービスを
   起動し、B-route 設定確認、scan、PANA、計測、MQTT の復帰を確認する。
7. 自然な adapter 応答停止、または管理された別の障害再現機会に、自動 sequence の
   logical reset → VBUS → trusted 再検証 → 計測までを確認する。手動の正の試験と
   自動 sequence の実証を区別して記録する。
8. Onyx 等の一時 disconnect を確認し、USB 再出現、NetworkManager、SORACOM、Napter
   および他機器の機能を別途確認する。未復帰は B-route 計測復帰と分けて扱う。

`ttyUSB0 → ttyUSB1` と旧 tty を別 FT230X が取得する競合、by-id 生成遅延、重複候補、
消失時 authorization は自動テストで厳密に確認する。実機で無理に番号変更を誘発せず、
stable by-id の実機復帰確認と分けて記録する。

残存限界は USB serial の偽装、kernel / udev と open 間の非原子的な hotplug、
hub 全体への電源影響、期限外・矛盾した故障状態の自動復旧不可である。
open 前後の検証は競合窓を狭めるが、暗号学的 identity や kernel の原子的な
identity 固定 open を実現するものではない。
