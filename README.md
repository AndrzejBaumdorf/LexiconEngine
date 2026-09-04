# LexiconEngine
完全自分用語彙力トレーニングエンジン

## 現在できること

- 単語、言語、品詞、複数の語義、例文、類義語・対義語、出典、難易度をSQLiteに保存
- 英→日、日→英、例文穴埋め、類義語・対義語の4択問題
- 問題形式ごとの回答履歴と直近10問の正答率
- 直近の不正解を重くするpriority出題
- JSONから単語データを構造化して取り込み
- CSVから単純な単語データを補助的に取り込み

## 起動

```powershell
py src/main.py --init
```

問題形式を選ぶと出題が始まります。終了は `q` です。教材データは `materials/` に置いてください。このディレクトリとDBはGit管理対象外です。

## JSON形式

JSONは単語オブジェクト、または単語オブジェクトの配列にします。`meanings`、`examples`、`synonyms`、`antonyms` は複数指定できます。

```json
[
	{
		"word": "abandon",
		"language": "English",
		"part_of_speech": "verb",
		"difficulty": "B2",
		"source": "The Japan Times EX",
		"meanings": ["放棄する", "断念する", "見捨てる"],
		"examples": [
			{
				"sentence": "They abandoned the plan.",
				"translation_ja": "彼らは計画を断念した。"
			}
		],
		"synonyms": ["forsake", "relinquish"],
		"antonyms": ["retain", "preserve"]
	}
]
```

```powershell
py src/main.py --import-json materials/words.json
```

## CSV形式（補助）

必須列は `word,meaning_ja` です。その他の列は任意です。

```csv
word,language,part_of_speech,meaning_ja,example_sentence,translation_ja,difficulty,source
abundant,English,形容詞,豊富な,The region has abundant natural resources.,,B2,The Japan Times EX
```

```powershell
py src/main.py --import-csv materials/words.csv
```
