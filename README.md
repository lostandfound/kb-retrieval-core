# kb-retrieval-core

`kb-retrieval-core` は、構造化 Markdown ナレッジベースを決定的で出典参照可能な evidence に変換する、ドメイン非依存の Python パッケージです。

## 範囲

提供する機能:

- Snapshot、`graph.json`、`references.yml` の読み込み
- 見出し単位の決定的 chunking
- SQLite に永続化した character n-gram postings による決定的 lexical 検索
- 任意 vector embedding の契約・fingerprint・SQLite sidecar
- vector-only 検索、entity-level RRF、lexical/vector/hybrid orchestration
- passage、relation、Claim の provenance 保持
- Recall@k / MRR 評価と consumer admission gate
- オフライン JSON CLI

このパッケージは knowledge-base authoring、ontology validation、LLM、prompt、HTTP API、UI、認証、会話状態を扱いません。Markdown と生成 graph が source of truth で、検索 index と vector sidecar は再生成可能な derived artifact です。

## インストールとテスト

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[test]'
PYTHONPATH=src python3 -m pytest
```

外部 embedding SDK、モデルファイル、ネットワークは不要です。`DeterministicTestEmbedder` と `InjectedEmbedder` はテストおよびオフライン検証用です。

## CLI

受け入れ fixture を使った lexical 検索:

```bash
kb-retrieval build \
  --content-root tests/fixtures/acceptance/content \
  --graph tests/fixtures/acceptance/graph.json \
  --references tests/fixtures/acceptance/references.yml \
  --eval tests/fixtures/acceptance/evals/rag-eval.yml \
  --index .retrieval

kb-retrieval search "teaches" --index .retrieval --mode lexical
kb-retrieval search "teacher" --index .retrieval --expand-graph
kb-retrieval context "teaches" --index .retrieval
kb-retrieval inspect /entities/source.md --index .retrieval
kb-retrieval eval --index .retrieval --mode lexical
```

すべての出力は JSON です。構文エラー、入力エラー、未構成の vector resource は stderr に JSON 診断を出し、非ゼロ終了します。

vector / hybrid mode は、あらかじめ作成した SQLite vector sidecar を `--vector-index` で指定します。sidecar の manifest に保存された embedding configuration と lexical index の hash が検証されます。

オフラインの仕組み検証用 sidecar はCLIから構築できます。このコマンドは
`DeterministicTestEmbedder` 専用であり、consumer admission や本番 embedding
品質の根拠にはなりません。

```bash
kb-retrieval vector-build \
  --index .retrieval \
  --vector-index .retrieval-vectors \
  --dimension 8
```

```bash
kb-retrieval search "teaches" \
  --index .retrieval \
  --vector-index .retrieval-vectors \
  --mode hybrid
```

vector retrieval は optional かつ default-disabled です。consumer が実データで lexical baseline を上回ることを確認するまで、既定検索モードを変更しないでください。

## Python API

主要な入口は `kb_retrieval_core` の公開 export です。

- `load_snapshot`, `chunk_snapshot`
- `LexicalIndex`, `SQLiteIndex`
- `EmbeddingConfig`, `InjectedEmbedder`, `DeterministicTestEmbedder`
- `SQLiteVectorSidecar`, `VectorRetriever`
- `RRFConfig`, `fuse_entity_rankings`
- `HybridRetriever`, `RetrievalConfig`
- `evaluate`, `EvaluationProfile`, `compare_evaluations`
- `assemble_context`

検索結果は entity path、section、passage source IDs、適用された relation / Claim の status・confidence・path を保持します。Claim path は entity path と同様に評価可能な evidence path です。グラフ展開は明示的な `--expand-graph` 指定時だけ実行され、適用設定が結果に記録されます。source IDs は内部で bare ID に正規化され、context assembly が references と解決します。

## 設計資料とリリース基準

- [Architecture implementation direction](docs/architecture.md): package boundary、provenance、実装順序、Milestone 3 acceptance gate
- [Issue breakdown](docs/ISSUES.md): 実装単位と完了条件

現在はMilestone 4（retrieval integration contract）の完了前です。検索機能は
利用できますが、別RAGアプリ向けの公開serialization契約、実効検索設定の完全な
再現性、consumer regression profile、安定版監査が残っています。

安定版リリース前には `docs/architecture.md` のMilestone 4 completion gateと
`docs/ISSUES.md` のIssue #10〜#14をすべて満たす必要があります。consumer-owned
admission profileに合格したconsumerだけがvector/hybridを既定有効化できます。

## License

MIT
