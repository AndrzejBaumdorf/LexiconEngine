# LexiconEngine
完全自分用語彙力トレーニングエンジン

## 現在できること

- 単語、言語、品詞、複数の語義、例文、類義語・対義語、出典、難易度をSQLiteに保存
- 英→日、日→英、例文穴埋め、類義語・対義語の4択問題
- 問題形式ごとの回答履歴と直近10問の正答率
- 直近の不正解を重くするpriority出題
- JSONから単語データを構造化して取り込み

## 起動

```powershell
py src/main.py --init
```

問題形式を選ぶと出題が始まります。終了は `q` です。教材データは `materials/` に置いてください。このディレクトリとDBはGit管理対象外です。

## JSON形式

JSONは単語オブジェクト、または単語オブジェクトの配列にします。単語の下に品詞、品詞の下に意味を置きます。類義語・対義語は意味の下に置き、関連する単語・品詞・意味を指定します。

```json
[
	{
		"word": "abandon",
		"language": "English",
		"difficulty": "B2",
		"source": "The Japan Times EX",
		"parts_of_speech": [
			{
				"part_of_speech": "動詞",
				"meanings": [
					{
						"meaning_ja": "放棄する",
						"synonyms": [
							{
								"word": "forsake",
								"part_of_speech": "動詞",
								"meaning_ja": "見捨てる"
							}
						],
						"examples": [
							{
								"sentence": "They abandoned the plan.",
								"translation_ja": "彼らは計画を断念した。"
							}
						]
					}
				]
			}
		]
	}
]
```

```powershell
py src/main.py --import-json materials/words.json
```
