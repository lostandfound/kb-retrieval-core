---
name: find-book
description: NDL サーチ（国立国会図書館）API で書籍・資料を検索し、文献レジストリ references.yml に登録する手順。「書籍を探して」「NDLで検索」「書誌を確認して」で使用。論文検索は find-paper（CiNii）を使う。
---

ナレッジベースの出典として書籍・単行資料を探し、文献レジストリに登録するときは以下の手順を実行する。規約の正本は `CONTRIBUTING.md` の sources 節。まずリポジトリルートの `kb-domain.yml` を読み、`domain.content_root` を把握する。

1. `python3 scripts/ndl_search.py "<検索語>" --count 5 --format json > <作業用ディレクトリ>/search-result.json` を実行する（作業用ディレクトリはリポジトリ外のスクラッチ領域。`search-result.json`・`reference.yml` はリポジトリに含めない）。API は認証不要。ノイズが多い場合は `--title-only` でタイトル検索に絞る。雑誌は `--mediatype periodicals`。人向けの登録案が必要なら `--format yaml`（既定）を使う。

2. 出力される references.yml 登録案から、対象エンティティの主張を実際に裏付けられそうな文献を選ぶ。タイトルだけで判断できない場合は URL（NDL サーチの書誌ページ）を WebFetch して内容を確認する。

3. JSON 出力は `kb reference spec --from search-result.json --output reference.yml --dry-run`（いずれも作業用ディレクトリ内のパス）で決定論的な spec に変換し、内容を確認してから `kb reference create --from reference.yml --dry-run` で登録差分を確認する。登録 ID は規約に沿って編集する（著者ローマ字姓-年、例: `miyagi-1934`。仮 ID は必ず直す）。`<content_root>/references.yml`（content_root は `kb-domain.yml` の `domain.content_root`）への反映は `kb reference create --from reference.yml` を使う。フィールドは確認できた値のみ書く。特定系統内の資料と判断できる場合は optional key `lineage`（系統名の文字列。単位の例は `kb-domain.yml` の `domain.lineage_example`）を付与する（判断がつかない場合は省略）。

4. 出典として使うエンティティの `sources` に `- "ref: <id>"` を追記する。本文の主張は必ず自分の言葉で書く。資料本文・スキャンのファイルは `content_root` 配下に保存しない。パブリックドメインが確認できた資料の翻刻テキストのみ、`kb-domain.yml` の `corpus_root` が指すコーパス（規約は CONTRIBUTING.md §13 とコーパスの README）へ置ける。

5. `python3 scripts/validate.py` でエラーゼロ、URL を登録した場合は `--check-urls` も実行する。

6. コミットする。pre-commit hook が最終検証を行う。
