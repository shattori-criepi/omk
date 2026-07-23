# 開発ガイド

## 1. 目的

本書は、OMKをWindows、Linux、Raspberry Piで開発・テスト・実機確認するための共通手順を定義します。

実際のスクリプトやComposeファイルが本文書と異なる場合は、単に本文書を無視せず、どちらを正とするか判断して同時に更新してください。

## 2. 開発の基本フロー

```text
Issue / 目的を確認
      ↓
関連文書と契約を読む
      ↓
実機依存か非依存かを分類
      ↓
モックで失敗を再現するテストを作る
      ↓
小さな変更を実装する
      ↓
format / lint / typecheck / unit test
      ↓
Compose統合テスト
      ↓
必要な開発者だけがWindowsでスマートメータBルート実機テスト
      ↓
Raspberry Pi 4とRaspberry Pi 5の両方で最終実機テスト
      ↓
文書・設定例・変更履歴を更新
```

## 3. 前提ツール

### 共通

- Git
- Docker EngineまたはDocker Desktop
- Docker Compose plugin
- VS Code

### TypeScript開発

- Node.jsのバージョンはリポジトリのバージョン管理ファイルで固定する
- パッケージマネージャはlockfileに合わせ、一つへ統一する
- TypeScriptは`strict`を有効にする

### Python移行コード

- Pythonのバージョンをコンテナイメージで固定する
- 依存は`pyproject.toml`等で管理する
- ホストへ手作業で依存をインストールする手順を標準にしない

### WindowsでBルート実機を使う場合（任意）

- Windows 11とWSL2
- WindowsからWSL2へUSBデバイスを公開できる構成
- Dockerから対象シリアルデバイスへアクセスできる権限
- Bルート資格情報を秘密管理する方法

### Raspberry Pi 4／5

- 64-bit Raspberry Pi OSのみを使用する
- Docker EngineとCompose plugin
- 対象デバイスのホスト設定
- udev、Bluetooth、LTE、ディスプレイの設定

## 4. 環境別の起動

### 4.1 Windows／モック

```bash
cp .env.example .env
docker compose -f compose.yaml -f compose.mock.yaml up --build
```

期待する状態:

- `core`が起動する
- シミュレータが計測値を生成する
- UIで現在値を確認できる
- ローカル保存とOutboxを確認できる
- 実機デバイスがなくてもエラー終了しない

停止:

```bash
docker compose -f compose.yaml -f compose.mock.yaml down
```

データも削除する場合だけ明示的にvolumeを削除します。

```bash
docker compose -f compose.yaml -f compose.mock.yaml down --volumes
```

### 4.2 Windows／Bルート実機（任意）

Bルート対応USBドングルをWindowsへ接続し、WSL2と対象コンテナから利用できる状態にしてから起動します。この手順は、開発中にスマートメータ実機との接続を確認する必要がある開発者だけが使用します。

```bash
cp .env.example .env
docker compose -f compose.yaml -f compose.windows-hardware.yaml up --build
```

期待する状態:

- Bルート実機アダプタが起動する
- Bルート以外の入力はモックへ差し替えられる
- 接続、認証、計測、切断、再接続を確認できる
- USB再接続後の復旧を確認できる
- 資格情報がログへ出ない

この環境は任意の開発検証経路であり、通常開発、CI、本番運用の要件には含めません。正式な対応判定は、Raspberry Pi 4とRaspberry Pi 5の両方で同じテストを実施して行います。

### 4.3 Raspberry Pi 4／5の実機

ホスト側の周辺機器設定を確認してから起動します。

```bash
cp .env.example .env
docker compose -f compose.yaml -f compose.pi.yaml up -d --build
```

状態確認:

```bash
docker compose -f compose.yaml -f compose.pi.yaml ps
docker compose -f compose.yaml -f compose.pi.yaml logs --tail=200 core
```

Raspberry Pi 4またはRaspberry Pi 5の実機で問題が起きた場合は、アプリログだけでなく次を分けて確認します。

1. ホストOSがデバイスを認識しているか
2. 権限とudev名が正しいか
3. コンテナへデバイス／ソケットが渡っているか
4. ドライバが接続できるか
5. 共通データへ変換できるか
6. 保存・送信できるか

## 5. 設定管理

### 5.1 ファイルの役割

- `.env.example`: 必要な環境変数名、説明、安全な例
- `.env`: ローカル実値。Git管理しない
- `config/schemas/`: 設定スキーマ
- `config/example/`: 公開可能な設定例
- Docker secrets: 本番の秘密値

### 5.2 起動時検証

設定は起動直後に検証し、次を明確に報告します。

- 欠けているキー
- 型の誤り
- 許容範囲外の値
- 存在しないドライバ種別
- 重複するID
- 無効なデバイスパス

秘密値そのものをエラーメッセージへ含めません。

## 6. TypeScript実装規約

### 6.1 コンパイラ

- `strict: true`
- `noUncheckedIndexedAccess: true`を検討・推奨
- `exactOptionalPropertyTypes: true`を検討・推奨
- `any`は原則禁止。外部入力は`unknown`から検証する
- JSON、MQTT、環境変数、DB結果を型アサーションだけで信用しない

### 6.2 モジュール

- 他モジュールの内部パスを直接importしない
- 各モジュールは公開エントリーポイントを持つ
- DomainはAdapterをimportしない
- `bootstrap`以外で具象クラスを組み立てない
- 循環依存をCIで検出できるようにする

### 6.3 エラー

- ドメインエラー、設定エラー、一時的外部エラー、恒久的外部エラーを区別する
- `catch`して無視しない
- 再試行可能性をエラー型または結果型で表現する
- ユーザー向けメッセージと運用ログを分ける
- 秘密値や生payloadを不用意に例外へ含めない

### 6.4 非同期処理

- 無制限の並列処理を避ける
- タイムアウトを明示する
- `AbortSignal`等で停止可能にする
- 終了時に計測・保存・送信を安全に停止する
- 再送処理にバックプレッシャーを持たせる

## 7. Python移行コードの規約

Pythonコードは、既存資産を安全に移行するための境界として扱います。

- 外部送信契約を明示する
- 入力・出力をJSON Schema等で検証する
- 型ヒントを付ける
- 設定と秘密値を環境変数／secretから受け取る
- コンテナの標準出力へ構造化ログを出す
- 再送・重複・タイムアウトの動作をテストする
- TypeScriptコアと同じDBを直接書き換える構成は避ける
- 統合または廃止の完了条件を`docs/roadmap.md`へ記録する

## 8. テスト戦略

### 8.1 単体テスト

対象:

- 計測値の正規化
- 単位変換
- 時刻処理
- バリデーション
- 異常値判定
- Outbox登録と再送判断
- 設定検証
- デバイス状態遷移

外部ネットワーク、実DB、実時刻、実デバイスを使いません。

### 8.2 契約テスト

対象:

- `SensorDriver`
- `MeasurementRepository`
- `UploadTarget`
- MQTT payload
- HTTP API
- ESP32メッセージ

すべてのドライバ実装に同じ契約テストを適用します。

例:

```ts
export function sensorDriverContract(
  name: string,
  createDriver: () => Promise<SensorDriverTestHarness>,
): void {
  describe(name, () => {
    it("starts and stops safely", async () => {
      // 共通契約を検証する
    });

    it("emits canonical measurements", async () => {
      // Measurementスキーマを検証する
    });

    it("reports a recoverable disconnect", async () => {
      // 一時切断時の状態遷移を検証する
    });
  });
}
```

### 8.3 統合テスト

Docker Composeで次を検証します。

- simulator → core → storage
- ESP32相当MQTT payload → core
- storage → outbox → uploader
- broker再起動後の再接続
- DB再起動後の復旧
- 外部送信失敗と再送
- スキーマ不正データの隔離

### 8.4 実機テスト

実機でしか確認できない部分だけを対象にします。WindowsでのBルート実機確認は任意の開発経路とし、本番適合性はRaspberry Pi 4とRaspberry Pi 5の両方で判定します。

- Windows／WSL2でのBルートUSB認識、権限、再接続
- Raspberry Pi 4およびRaspberry Pi 5でのUSB認識と権限
- Wi-SUN通信
- Bluetooth接続
- LTE接続
- ディスプレイ
- Raspberry Pi再起動
- 長時間運転

### 8.5 回帰テストデータ

実機から取得したデータをfixture化する場合:

- 認証情報を除去する
- 住宅・利用者を特定できないようにする
- 最小限の長さへ切り出す
- 期待する挙動をREADMEへ記載する
- バイナリプロトコルの場合は生成元と形式を明示する

## 9. 新しいセンサードライバの追加

### 9.1 追加前

次を確認します。

- 何を計測するか
- 必要精度と周期
- 接続方式
- Raspberry Pi対応
- Windowsでのモック方法、および必要な場合だけ使うBルート実機接続方法
- 電源・設置負担
- 製品寿命と代替候補
- データ量
- 校正方法

### 9.2 実装手順

1. 共通metric・単位が既にあるか確認する
2. ドライバ設定スキーマを追加する
3. `SensorDriver`を実装する
4. ベンダー形式から`Measurement`へ変換する
5. モック／シミュレータを実装する
6. 共通契約テストを適用する
7. 切断・復旧・不正値のテストを追加する
8. Composeのmock／pi設定を更新する
9. `docs/hardware.md`へセットアップと制約を追加する
10. 実機スモークテスト結果を記録する

### 9.3 完了条件

- 実機なしでCIが通る
- 実機で一定期間計測できる
- 切断後に自動復旧する
- 他ドライバを停止させない
- 秘密値をログへ出さない
- 共通データ形式へ正規化される
- 設定例と文書がある

## 10. データスキーマ変更

共通`Measurement`、MQTT payload、API、DB schemaを変更する場合:

1. 互換性の影響を確認する
2. スキーマバージョンを更新するか判断する
3. 読み取り側を先に互換対応する
4. fixtureと契約テストを更新する
5. 保存済みデータの移行方法を用意する
6. ESP32等の段階更新を考慮する
7. 文書を更新する

破壊的変更を同時一斉更新だけに依存させないでください。

## 11. ログと診断

### 11.1 ログの例

```json
{
  "timestamp": "2026-01-01T00:00:00.000Z",
  "level": "warn",
  "service": "core",
  "module": "ble-watt-checker",
  "event": "device_disconnected",
  "deviceId": "watt-checker-living",
  "retryInMs": 5000,
  "message": "Bluetooth device disconnected; reconnect scheduled"
}
```

### 11.2 出力してはいけないもの

- BルートID・パスワード
- APIキー・証明書秘密鍵
- MQTTパスワード
- 実住宅の住所や利用者名
- 外部APIのAuthorization header

### 11.3 診断コマンド

将来的に`scripts/diagnose`等へ集約します。

- OS・アーキテクチャ・バージョン
- Docker／Composeバージョン
- デバイス一覧
- udevリンク
- Bluetooth状態
- LTE疎通
- ディスク空き容量
- コンテナヘルス
- 最終計測・最終送信

診断出力は秘密情報をマスクします。

## 12. CIで必須にする項目

- Markdown link check
- format check
- lint
- TypeScript typecheck
- unit tests
- contract tests
- build
- Docker image build for target architectures
- dependency vulnerability check
- secret scan
- Compose config validation

Raspberry Pi実機テストは通常CIと分離し、リリース前ゲートとして実施します。

## 13. ブランチと変更単位

特定のGit運用方式は未決定ですが、変更単位は次を守ります。

- 一つのPR／変更は一つの目的に絞る
- 無関係な整形・改名を混ぜない
- 設計変更と単純修正を混ぜない
- 自動生成物を必要以上にコミットしない
- 破壊的変更は移行手順を含める

コミットメッセージは、何をしたかだけでなく、必要に応じてなぜ変更したかが分かるようにします。

## 14. Definition of Done

変更は次を満たして完了とします。

- 目的と受け入れ条件を満たす
- 型チェック、lint、テストが通る
- 必要なモックまたはfixtureがある
- 実機依存変更はRaspberry Pi 4とRaspberry Pi 5の両方で確認した
- ログとエラー処理がある
- 秘密情報が含まれていない
- 設定例が更新されている
- API、MQTT、データ形式の契約が更新されている
- READMEまたはdocsが更新されている
- 未決定事項を勝手に確定していない

## 15. Codex／生成AIを使うときの作業規則

### 15.1 作業開始時に読ませるもの

1. `README.md`
2. `docs/architecture.md`
3. `docs/design-principles.md`
4. 対象に応じて`docs/hardware.md`または本書
5. 関連する契約、設定スキーマ、テスト

### 15.2 依頼に含めるもの

- 変更目的
- 変更してよい範囲
- 変更してはいけない契約
- 実機の有無
- 完了条件
- 実行すべきテスト

依頼例:

```text
スマートメータドライバの再接続処理を修正してください。
README.md、docs/architecture.md、docs/hardware.mdを先に読み、
Measurementsモジュールの公開契約は変更しないでください。
実機は使えないため、記録済みfixtureとモックで失敗を再現するテストを追加してください。
変更後に型チェック、単体テスト、ドライバ契約テストを実行してください。
```

### 15.3 AIに禁止すること

- 不明なハードウェア仕様を推測で実装する
- 既存の秘密値をコードやテストへコピーする
- 理由なく依存ライブラリを追加する
- `privileged: true`で問題を回避する
- テストを削除して通す
- 公開契約を説明なく破壊する
- unrelatedな大規模リファクタリングを混ぜる
- 文書と実装の不一致を放置する

### 15.4 AIの変更結果に求める報告

- 変更したファイル
- 変更理由
- 影響する契約
- 実行したテストと結果
- 実機未確認事項
- 残るリスクと未決定事項

## 16. よくある問題の切り分け

### デバイスが見えない

1. ホストの`lsusb`、`/dev`、Bluetooth状態を確認
2. udevリンクと権限を確認
3. Composeのdevice／volume設定を確認
4. コンテナ内から存在を確認
5. ドライバ設定のパス・IDを確認

### 計測できるが送信できない

1. ローカル保存の成功を確認
2. Outbox件数を確認
3. ホストの外部疎通を確認
4. DNSを確認
5. 認証設定を確認
6. uploaderログのstatus codeと再送予定を確認

### Windowsでは動くがRaspberry Pi 4／5で動かない

1. CPUアーキテクチャ対応を確認
2. ネイティブ依存のビルドを確認
3. コンテナユーザーのデバイス権限を確認
4. 改行・パスの差ではなく、実機Adapter境界を確認
5. Raspberry Pi OSとライブラリ対応版を確認

### 再起動後にデータが欠ける

1. 保存volumeの永続化を確認
2. graceful shutdownを確認
3. 書き込みトランザクションを確認
4. Outboxと計測保存の整合性を確認
5. OS時刻とファイルシステムエラーを確認

## 17. 未決定事項

Raspberry Pi 4とRaspberry Pi 5の両方を正式対応とし、64-bit Raspberry Pi OSのみを正式対象とする点は決定済みです。

- TypeScriptのNode.js版とパッケージマネージャ
- UIフレームワーク
- Python依存管理ツール
- CIサービス
- AMD64／ARM64マルチアーキテクチャイメージのビルド方式
- formatter／linterの具体構成
- Gitブランチ運用
- リリースバージョニング
- 実機試験結果の保存形式

これらは実装開始時に最小限を決定し、判断理由を記録します。

