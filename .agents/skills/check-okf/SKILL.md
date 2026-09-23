---
name: check-okf
description: OKF v0.2 bundle適合性・strict export・決定性確認。「OKFチェック」「OKF準拠確認」「bundle validate」など、OKF bundle の検証依頼に使用する。内部KBの一般validateだけの依頼には使用しない。
---

# OKF v0.2 bundle を検証する

JSON diagnostics と warnings、および終了コードを判定の正本とする。LLMの独自判断で合否を上書きしない。入力や内部KBを変更せず、外部ネットワークも使わない。失敗は `code`・`path`・`message` と、`hard`（不合格）/`advisory`（warning）を区別して報告する。意味的な出典の妥当性は検査対象外である。

既存のOKF bundleを確認する場合は、次を実行する。

```bash
kb okf validate PATH --strict --format json
```

内部KBから確認する場合は、次の順序を固定する。

1. `kb validate --format json` で内部KBを検証する。
2. `mktemp -d` で一時ディレクトリを作り、その配下に2箇所の出力先を用意する。作成直後に `trap 'rm -rf "$tmpdir"' EXIT INT TERM` を設定し、途中のどの失敗でもcleanupされるようにする。
3. 各出力先へ `kb export okf --output PATH` を実行する。
4. 2つの出力をそれぞれ `kb okf validate PATH --strict --format json` で検証する。
5. `diff -qr` で2つの出力ディレクトリのfile treeと各ファイル内容が同一（byte単位）か確認する。
6. 成功時もtrapを通じて一時ディレクトリをcleanupする（手動cleanupに依存しない）。

各コマンドのJSON結果と終了コードを保全し、hard failure・advisory warning・決定性差分を分けて報告する。検証失敗時もsource mutationは行わない。
