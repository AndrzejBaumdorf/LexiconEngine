# LexiconEngine
完全自分用語彙力トレーニングエンジン

## 現在できること

- SQLiteに単語、日本語訳、例文、類義語・対義語、回答履歴を保存
- 英単語から日本語訳を選ぶ4択クイズ
- 直近10問の正答率を回答ごとに表示
- CSVから単語データを取り込み

## 起動

Python 3.10以上で実行します。

```powershell
python3 src/main.py --init
```

初回起動時は `data/lexicon.db` を作成し、動作確認用のサンプル単語を登録します。教材データは `materials/` に置いてください。このディレクトリとDBはGit管理対象外です。

## CSV形式

教材データの取り込みには、次の列を持つUTF-8 CSVを使います。

```csv
word,meaning_ja,example_sentence
word,日本語訳,Example sentence.
```

```powershell
python3 src/main.py --import-csv materials/words.csv
```

既存単語の `word` が一致する場合は、日本語訳と例文を更新します。
