# LexiconEngine
完全自分用語彙力トレーニングエンジン

## 現在できること

- 単語、言語、品詞、複数の語義、例文、類義語・対義語、出典、難易度をSQLiteに保存
- 英→日、日→英、例文穴埋め、類義語・対義語、派生語の4択問題 (選択肢は同じ言語の単語から作る)
- ドイツ語名詞の性・複数形 (意味ごと)、派生語の登録
- 問題形式ごとの回答履歴と直近10問の正答率
- 直近の不正解を重くするpriority出題
- JSONから単語データを構造化して取り込み

## 起動

```powershell
py src/main.py
```

DBを作り直すときは `--reset` を付けます。確認のあと、元のDBは `data/lexicon-backup-<日時>.db` に名前を変えて残し、空のDBから起動します (`-y` で確認を省略)。教材は「フォルダ取り込み」で入れ直してください。

```powershell
py src/main.py --reset
```

言語・出典・レベル・問題形式・出題数を選んで開始します。言語はDBに登録されている言語から選び、出題・選択肢・直近の正答率はその言語の単語だけで扱います。出典 (`source`) とレベル (`difficulty`) で出題する単語を絞り込めます。誤答の選択肢は範囲を絞っても同じ言語の単語全体から選びます。問題形式名は言語に合わせて「英→日」「独→日」のように変わります。

英語の例文穴埋めで穴の語が活用形 (petrified, accolades など) のときは、選択肢も同じ形 (過去形・過去分詞・-s・-ing) にそろえます。不規則動詞は `src/english.py` の表で活用させます (over- などの接頭辞付きにも対応)。

クイズ中のショートカット: `1`〜`4` 選択肢を選ぶ / `Enter` 回答・次の問題へ / `S` スキップ (不正解として記録) / `Esc` 中断。新しくDBを作ると `samples/sample_words.json` が取り込まれます。

教材データは `materials/` に置いてください。このディレクトリとDBはGit管理対象外です。サブフォルダで整理できます。

```
materials/
├── English/
│   └── Japan Times EX1級/
│       ├── unit01.json
│       └── unit02.json
└── German/
    └── 白水社/
        ├── basic.json
        └── A.json
```

- エディタの保存先にはサブフォルダ内のJSONも一覧に出ます。`German/白水社/B.json` のように存在しないパスを入力すると、フォルダごと新しく作ります。
- 一番上のフォルダ名が言語名 (English / German) のとき、空のファイルを開くと言語の初期値がそのフォルダ名になります。
- クイズ画面の「フォルダ取り込み」で、選んだフォルダ以下のJSONをまとめてDBに取り込めます。

## GUIで単語登録・編集

```powershell
py src/word_editor.py
```

左の一覧から単語を選ぶと内容を読み込み、品詞・意味・例文などを編集して上書き保存できます。「新規」で新しい単語を入力し、保存すると `materials/` のJSONに下記の形式で追加されます。新規入力した単語が登録済みなら、品詞・意味単位で追記するか確認します。

- ショートカット (WindowsはCtrl): `⌘S` 保存 / `⌘N` 意味欄の内容を確定して次の意味へ (一覧で選んだ意味を編集中なら更新) / `⌘⇧N` 新しい単語
- 類義語・対義語は `forsake=見捨てる` のように意味も書くと英→日の出題にも使われます。
- 派生語は `placatory(形容詞)=なだめるような, placation(名詞)` の形で書きます。意味は省略できます。
- 語形の欄は言語と品詞に応じて表示されます。German の名詞では性 (m/f/n)・複数形、動詞では現在3人称単数・過去形・現在完了 (`hat gemacht` / `ist gegangen`) を入力します。der See / die See や hängen (hing / hängte) のように意味で変わる語があるため、意味ごとに設定します。登録した語形は例文穴埋めで穴にする語として使われ (現在完了は最後の語)、出題時は `das Haus (複数: Häuser)` `gehen (geht - ging - ist gegangen)` のように表示されます。
- 例文の穴埋め箇所は活用形 (petrified, abandoned, ドイツ語の語尾変化など) から自動で探します。見つからない不規則形は `They [overrode] it.` のように `[ ]` で囲みます。分離動詞は `Er [fängt] morgen [an].` のように複数囲めます。

DBへの反映はクイズ画面の「JSON取り込み」で行います。取り込みではJSONの内容を正とし、JSONから消した意味・例文・関連語はDBからも消えます。JSONから単語ごと削除した場合は、DB側には残ります。

## 言語ごとの仕様

品詞の候補、名詞の性と冠詞、品詞ごとの語形の欄 (`form_fields`)、例文穴埋めでの活用形の推測は `src/languages.py` に言語ごとにまとめています。新しい言語は `Language(...)` を定義して `LANGUAGES` に加えると、エディタの言語欄・品詞欄、穴埋め、出題時の冠詞表示に反映されます。未登録の言語名で登録した単語は、性なし・活用推測なし (例文中の完全一致か `[ ]` 指定のみ) で扱います。

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
		],
		"derivatives": [
			{
				"word": "abandonment",
				"part_of_speech": "名詞",
				"meaning_ja": "放棄"
			}
		]
	},
	{
		"word": "See",
		"language": "German",
		"parts_of_speech": [
			{
				"part_of_speech": "名詞",
				"meanings": [
					{ "meaning_ja": "湖", "gender": "m", "plural": "Seen" },
					{ "meaning_ja": "海", "gender": "f" }
				]
			}
		]
	},
	{
		"word": "gehen",
		"language": "German",
		"parts_of_speech": [
			{
				"part_of_speech": "動詞",
				"meanings": [
					{ "meaning_ja": "行く", "present_3sg": "geht", "past": "ging", "perfect": "ist gegangen" }
				]
			}
		]
	}
]
```

例文の `targets` は穴埋めにする語を本文中の出現順に並べたものです (エディタの `[ ]` から自動で作られます)。旧形式の `"related_words": {"noun": "tenacity"}` も派生語として取り込めます。

