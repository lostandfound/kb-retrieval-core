# RAGアプリ統合ガイド

`kb-retrieval-core`は検索とEvidence Packetの組み立てだけを担当します。
LLM、prompt、回答生成、会話履歴、HTTP API、認証、UIはRAGアプリ側に置きます。

## 基本フロー

```python
from kb_retrieval_core import (
    GraphExpansionConfig,
    RetrievalConfig,
    assemble_context,
    build_retriever,
    load_snapshot,
)

snapshot = load_snapshot(content_root, graph_path, references_path)
retriever = build_retriever(snapshot)
config = RetrievalConfig(
    mode="lexical",
    top_k=5,
    graph=GraphExpansionConfig(enabled=True),
)
hits = retriever.search(user_query, config=config)
context = assemble_context(hits, snapshot, strict=True)
packets = context.to_dict()["packets"]
```

`content_root`、`graph_path`、`references_path`はconsumerが管理するKBの入力です。
SQLiteのテーブルやprivate helperを直接参照せず、同じ入力からindexを再生成できます。

## 検索設定

graph expansionは明示的に有効化します。`top_k`は最終Evidence Packet数で、展開時のseed poolは内部で拡張されます。評価・監査のため、検索結果の`metadata.graph_expansion`と評価JSONの`fusion_config`を保存してください。

vector/hybridを使う場合は、consumerがembedding fingerprintとsidecarの互換性を確認したうえで、`RetrievalConfig(mode="hybrid")`を選びます。lexical検索は外部サービスなしで動作するため、常に比較用baselineを残します。

## CitationとClaimの扱い

各packetには`entity_path`、`section`、`source_ids`、`passage_source_ids`、参照解決済みの`references`が含まれます。Claimまたは関係由来のpacketでは、さらに`claim_path`、`claim_status`、`confidence`、`relation_source_ids`が保持されます。

`requires_hedging`がtrue、またはClaim status/confidenceが付いた証拠は、回答生成時に断定を避けるconsumerポリシーを適用してください。Claimを通常の確定relationへ変換してはいけません。

本番RAGでは`strict=True`を推奨します。未解決sourceがある場合は回答を生成せず、KBまたはreferencesを修正してください。診断画面など明示的な用途だけ`strict=False`を使います。

## 契約

consumer contract testは公開exportだけをimportし、検索結果を`json.dumps`可能なEvidence Packetとして検証します。CLI JSONには`schema_version`が含まれるため、consumerはmajor versionごとの互換性ルールに従って保存・移行してください。
