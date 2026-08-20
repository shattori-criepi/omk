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
- `GPL-3.0-or-later`
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

意味を変えずに表記だけを直す場合でも、Markdown構造を壊さない。`docs/history/**`は履歴資料として扱い、現行仕様に合わせて内容を書き換えず、表記崩れやMarkdown表示上の問題だけを修正する。内部識別子、環境変数名、service名、file名等は文章上の都合だけで変更しない。

日本語文書では自然な日本語表記を優先し、英語のsource formatting慣習を機械的に持ち込まない。

## 検証

文書変更後は、少なくとも次を確認する。

- Markdown local links
- `git diff --check`
- 日本語本文の不要なsoft line break候補
- 日本語本文中の不要なASCII / Unicode whitespace
- `git status --short`

## 背景

2026-08の監査では、日本語本文中のU+000A soft line breakを307件除去した。非ASCII whitespaceは検出されなかった。この文書は監査報告ではなく、同じ表示崩れを再発させないための作成ルールである。
