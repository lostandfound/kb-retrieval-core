---
name: audit-harness
description: ハーネス文書群（AGENTS.md・CONTRIBUTING・スキル・エージェント・スクリプト）の整合性を監査し正本参照型で修正する手順。「整合性チェック」「ハーネス監査」「文書の陳腐化確認」で使用。大きな構造変更（語彙追加・スクリプト追加・規約変更）の後に実行する。
---

ハーネス文書群の整合性を監査するときは以下を実行する。監査は読み取り → 修正 → 検証の順。

着手前と修正前に `git status --short` で作業ツリーの状態を記録する。監査中に別プロセス（他エージェント・セットアップコマンド）がファイルを書き換えることがあり、読み取り時と修正時で内容が違うと判断を誤る。修正直前に対象ファイルを再読し、自分が変更していない差分は自分のコミットに混ぜない。

## 対象（すべて読む）

- `kb-domain.yml`（ドメイン定義。実態との乖離も監査対象）
- AGENTS.md（導入先が行数などの上限を定めている場合はその制約も確認する）
- CONTRIBUTING.md、README.md
- CLAUDE.md（プロジェクト用。AGENTS.md と同じく導線の過不足を見る）
- `docs/` 配下の現行文書すべて。任意の予約運用ファイル `docs/CONCERNS.md`（未整理の懸念・違和感の受信箱）と `docs/BACKLOG.md`（実施すると判断した将来作業）は、存在する場合のみ監査する。アーカイブ扱いのディレクトリは、導入先の README でアーカイブと明示されているかだけ見る
- `apm_modules/lostandfound/kb-harness-core/.apm/` 配下の全 SKILL.md・エージェント定義（**KB ハーネス資産の正本**。`.codex/`・`.claude/` 配下の同名資産は `apm install` による生成物 — 正本と配布先の同一性も確認し、ドリフトがあれば正本を直して対象ランタイムへ再デプロイする）
- ルート `.apm/`（ドメイン固有エージェントの正本。パッケージ側と同様に配布先との同一性を確認する）
- `.codex/`・`.claude/`・`.agents/skills/` 配下のうちパッケージ外の資産（プロジェクト固有スキル等）
- apm.yml の `targets` と実在する配布先ディレクトリの対応（README/CONTRIBUTING が配布先として挙げるランタイムがすべて `targets` に含まれているか。抜けていると `apm install` がそのランタイムを更新せずドリフトが沈む。前例: codex 欠落、2026-09-03）
- apm.yml・`apm_modules/lostandfound/kb-harness-core/apm.yml`（依存宣言・パッケージ内容の一致）
- scripts/*.py の CLI 実引数（--help 相当）と docstring（正本はモジュール側 `apm_modules/lostandfound/kb-harness-core/scripts`。導入先ルートには symlink を張らず、このパスで直接呼ぶ）
- `kb --help` と各サブコマンドの `--help` 実出力 ↔ `docs/` 配下・`apm_modules/lostandfound/kb-harness-core/README.md`・各 SKILL.md が語るコマンド名とフラグ
- `<content_root>/vocabulary.yml`、`references.yml`（content_root は `kb-domain.yml` の `domain.content_root`）のスキーマ実態
- evals/ 配下、.env.example、.github/workflows/
- `.git/hooks/pre-commit` ↔ `apm_modules/lostandfound/kb-harness-core/scripts/hooks/pre-commit`（install-hooks.sh はコピー方式のため、正本更新後に再インストールしないと古い hook が走り続ける。diff で同一性を確認し、違えば `bash apm_modules/lostandfound/kb-harness-core/scripts/install-hooks.sh`）

## 監査の 7 観点

1. **文書↔実装の乖離** — 文書が語るコマンド・フラグ・パス・フィールド名が実在するか。スクリプトの実引数と文書の記述を突き合わせる
2. **文書間の矛盾** — 同じ規約が複数文書で違う値・表現になっていないか（述語・型固有フィールドの値域・sources 形式・レビュー工程）
3. **陳腐化ハードコード** — 語数・件数・列挙のハードコードで実態に追随していないもの。前例: 「述語 6 語」（2 回再発）。一覧を載せる文書は正本（vocabulary.yml 等）と突き合わせる
4. **新規要素の記載漏れ** — 最近増えたファイル・スキル・エージェント・スクリプトが、README / CONTRIBUTING / AGENTS.md の言及すべき箇所で欠けていないか（`git log --oneline -20` で最近の追加を把握）
5. **AGENTS.md の過不足** — 導入先が定める上限を守りつつ、現行ハーネスへの導線が過不足ないか
6. **設計文書の時制** — 「将来導入」「設計案」「導入後の候補」と書かれた事項が実装済みになっていないか。実装状況は vocabulary.yml・claims/ 等の実態と `git log` で確認し、完了したものは完了形に直し完了日を残す。前例: Claim と Organization（2026-09-03）
7. **検証コマンドの一致** — CI ワークフロー・pre-commit hook・AGENTS.md・CONTRIBUTING・本スキルの「検証」節が同じテストランナーと同じコマンド一式を指しているか。前例: CI が pytest 化した後も他が unittest のまま（2026-09-03）

## 修正方針

- **正本参照型**で直す: 値の重複記載を減らし、正となるファイル（vocabulary.yml / CONTRIBUTING / スクリプト自身）を指す記述にする。列挙をやむを得ず残す場合は「正は◯◯」と明記
- どちらが正か判断できない矛盾は修正せずユーザーへ報告
- 発見した不整合はすべて報告する（修正済み / 報告のみ を区別）。信頼性に関わる未整理の問題は、`docs/CONCERNS.md` が存在する場合はそこへ記録する。存在しない場合は監査結果で報告し、ユーザーから作成を求められていない限り新設しない

## 検証

修正後: `python3 -m pytest tests apm_modules/lostandfound/kb-harness-core/tests` 全パス（unittest discover はパッケージ側の pytest 形式テストを取りこぼす） + `kb validate` エラーゼロ + `kb sync --check` 差分なし + AGENTS.md が導入先の定める上限内。`.apm/` 正本を直した場合は `apm install --force` で全 `targets` に再デプロイし、正本と配布先の diff がないことを確認 → コミット（docs: / fix:、pre-commit hook 成功確認）。
