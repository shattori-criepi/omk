# Markdown文書スタイル

OMKのMarkdown文書を作成・更新するときは、読み手がrenderer上で自然に読めることを優先し、意味・Markdown構造・内部識別子を不用意に変えない。

## 日本語本文の改行ルール

日本語の通常本文は、1段落をMarkdown source上でも途中改行しない。文章を折り返す目的だけのsoft line breakを入れず、段落を分ける場合は空行を入れる。

Markdown rendererでは段落途中のU+000A LINE FEEDが空白として扱われる場合があり、`想定 します`、`優先度 など`、`PR作成を 求める`のような不自然な表示になる。長い1行になっても、日本語通常本文ではsource上の行長よりrenderer上の自然な日本語表示を優先する。

## 空白ルール

日本語本文中に不要なASCII spaceを入れない。Unicode whitespaceおよびinvisible characterも原則として入れない。日本語語句の途中、助詞と述語の間、読点・句点後などに不要な空白を入れない。

NG例:

- `想定 します`
- `優先度 など`
- `判断と 責任`
- `保証を 提供する`

OK例:

- `GitHub Discussions`
- `Pull Request`
- 英語文章内の通常の単語間space

## Markdown構造上の例外

次の要素では、Markdown構造や内容に必要な改行・空白を維持する。

- headings
- 箇条書き
- numbered lists
- tables
- blockquotes
- fenced code blocks
- inline code
- shell commands
- YAML、JSON、Python等のコード
- URLs
- Markdown links
- 意図的なhard line break

## 文書更新時の基本方針

意味を変えずに表記だけを直す場合でも、Markdown構造を壊さない。内部識別子、環境変数名、service名、file名等は文章上の都合だけで変更しない。

日本語文書では自然な日本語表記を優先し、英語のsource formatting慣習を機械的に持ち込まない。

## 読み手に合わせた表現

一般利用者向け文書では、必要な技術用語は残しつつ、抽象的な設計語より実際の操作や動作を記述する。たとえば、何を受信・保存・表示するのかを具体的に書く。開発者向け文書では、責務、境界、依存、抽象化、interfaceなど、設計や実装を正確に表す一般的な専門用語を機械的に平易化しない。

「正本」は原則として使わない。参照先、定義する場所、基準とする設定など、文脈に応じて実際の関係を書く。`canonical`も、コード上の識別子や既存の正式な技術概念を説明する場合を除き、文書の説明用語として安易に導入しない。通常のtopic、sensorを特定する前の入力など、実際の意味を記述する。

BLEの`advertising` / `advertisement`は日本語本文で「広告」と訳さない。開発者向けでは「BLEアドバタイズ」、一般向けでは必要に応じて「BLEセンサが送信する情報」や「BLE受信」と具体的に書く。identifier、API名、`ADV`、`Scan Response`は変更しない。

## 検証

文書変更後は、少なくとも次を確認する。

- Markdown local links
- `git diff --check`
- 日本語本文の不要なsoft line break候補
- 日本語本文中の不要なASCII / Unicode whitespace
- `git status --short`

## 背景

日本語段落の途中改行がrenderer上で不要な空白になることを防ぐため、このルールを設けている。
