# アパホテル料金トラッカー（GitHub 自動更新版）

楽天トラベルAPIを使って、アパホテルの料金を集めて公開するツールです。

- 毎朝自動で、福岡市・東京23区など指定エリアのアパホテルの日別最安値を集めます。
- 集めた料金は、Web 上のダッシュボードとして公開します。
- Mac の電源を切っていても動きます。費用はかかりません。

完成すると、ダッシュボードは次の URL で見られます。

```
https://ユーザー名.github.io/apa-rates/
```

---

## 手順1　GitHub アカウントを作る

1. https://github.com/signup で登録します（無料）。
2. 登録した**ユーザー名**を控えておきます。以下では `yourname` とします。

## 手順2　リポジトリを作る

1. 右上の「+」から「New repository」を開きます。
2. Repository name に `apa-rates` と入れ、**Public** を選んで「Create repository」を押します。
3. 次の画面で「uploading an existing file」をクリックします。
4. 解凍した `apa_rates` フォルダの**中身**を、すべてドラッグしてアップロードします。中身は次のとおりです。
   - `apa_rates.py`
   - `dashboard_template.html`
   - `config.json`
   - `README.md`
   - `ワークフロー（貼り付け用）.yml`
5. 下の「Commit changes」を押します。

## 手順3　自動実行の設定ファイルを置く

`.github` フォルダは Finder では見えないことがあります。そのため、ブラウザで作成します。

1. リポジトリ画面で「Add file」から「Create new file」を開きます。
2. ファイル名の欄に `.github/workflows/daily.yml` と入力します。`/` を打つとフォルダが自動でできます。
3. `ワークフロー（貼り付け用）.yml` の中身をすべてコピーし、本文に貼り付けます。
4. 「Commit changes」を押します。

## 手順4　楽天のキーを登録する（Secrets）

1. リポジトリの「Settings」から「Secrets and variables」→「Actions」を開きます。
2. 「New repository secret」で、次の2つを登録します。

| Name | Secret |
|---|---|
| `RAKUTEN_APP_ID` | 楽天のアプリID |
| `RAKUTEN_ACCESS_KEY` | 楽天のアクセスキー |

キーは暗号化して保存され、公開ページからは見えません。

## 手順5　楽天の「許可されたWebサイト」を変更する

楽天ウェブサービスのアプリ設定で、許可されたWebサイトを次のように変更します。

```
https://yourname.github.io
```

- `yourname` は自分の GitHub ユーザー名にします。**小文字**で書いてください。
- 末尾の `/` は付けません。

ツールはこの URL を名乗って楽天に接続します。
別の URL を使いたい場合は、Secrets に `RAKUTEN_ORIGIN` を追加し、その URL を登録してください。

## 手順6　Pages を有効にする

1. 「Settings」から「Pages」を開きます。
2. 「Build and deployment」の Source で **GitHub Actions** を選びます。

## 手順7　初回を手動で動かす

1. 「Actions」タブを開き、左の「毎朝の料金取得」を選びます。
2. 「Run workflow」から、もう一度「Run workflow」を押します。
3. 10分ほどで緑のチェックが付けば成功です。
4. `https://yourname.github.io/apa-rates/` を開くと、ダッシュボードが見られます。

以降は毎朝7時ごろに自動で実行されます。GitHub 側の都合で数十分遅れることがあります。

赤い × が付いた場合は、その実行をクリックし、赤い行を開いて表示されたメッセージを Claude に貼ってください。

---

## 設定を変えたいとき

GitHub 上で `config.json` を開き、鉛筆マークから編集します。

| 項目 | 内容 |
|---|---|
| `fetch_scope` | 料金を取る範囲です。`["福岡市", "東京23区", "大阪市"]` のように並べます。全国なら `["全国"]` ですが、1回に数時間かかります。 |
| `days_ahead` | 何日先まで取るか。初期値は45日です。 |
| `adults` | 1室あたりの大人の人数です。 |

## データについて

- 料金は楽天トラベル経由の1室1泊（税・サービス料込）の最安値です。公式サイトの価格とは異なる場合があります。
- 集めたデータは `data/prices/年-月.csv` に自動で保存されます。Excel でも開けます。
- ダッシュボードは誰でも見られる公開ページです。キーは公開されません。
