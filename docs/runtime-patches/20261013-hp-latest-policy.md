# Runtime policy addition

PARENT-WORKFLOW.mdのDaily V4節の直前へ追加した現行方針。生成親の権限は変更しない。復元時は同見出しの重複を作らない。

## HPへの最新完成版掲載

生成を担当する親は、従来どおりローカル版を確定し、公開・追加送信をしません。Hermesの外側の処理が生成終了後に `hq/harness/chart_intel_publication.py` を実行し、別プロセスの独立した内容・数値・PC/スマホ画面のレビューを経て `dev/fundamental-macro-analysis/scripts/hp_publication.py` からHPへ掲載します。

HPは各種類1本の最新完成版を表示し、元の資料基準日時を保持します。古い完成版を当日の新規分析と扱いません。生成失敗や独立レビュー不合格では前の公開版を保ちます。過去分のMarkdownと検証記録は `reports/<kind>/editions/` に残し、前の完成版HTMLは保存したMarkdownとの照合・現在参照の保護後に整理します。親がこの後処理のレビューを代行したり、自己レビューから公開可能フラグを立てたりしません。

