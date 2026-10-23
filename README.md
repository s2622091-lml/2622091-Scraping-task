# 武蔵野大学 Webサイトのサイトマップ

大学のトップページから同じドメインのリンクをたどり、ページのURLとタイトルをCSVに保存する課題です。

## 実行方法

Python 3.9以降を使います。追加のライブラリは必要ありません。

```bash
python3 scraper.py
```

`sitemap.csv`に`url,title`の2列を保存します。Excelで日本語が文字化けしにくいUTF-8 BOM付きのCSVです。

## 処理の流れ

1. トップページをキューに入れる。
2. ページのHTMLから`<title>`と`<a href>`を取得する。
3. `pages[URL] = タイトル`という辞書に保存する。
4. 未訪問で、同じホストのリンクをキューに追加する。
5. キューが空になるまで繰り返し、辞書からCSVを書き出す。

`time.sleep()`でリクエストの間隔を最低1秒あけます。robots.txtに指定があればそれにも従います。画像、PDFなどHTMLではないファイルは対象外です。外部サイトや別サブドメインにはアクセスしません。ページ内の`#`以降と広告計測用のパラメータは取り除きますが、ページを指定するクエリは残します。

## 中断・再開

途中でCtrl+Cを押してもCSVと進捗を保存します。同じコマンドを再実行すると再開できます。進捗ファイルは`.crawl-state.json`です。最初からやり直す場合は`--fresh`を指定します。

通信障害やサーバーの一時的なエラーが起きた場合も、進捗を保存して終了します。接続が戻ってから同じコマンドを再実行すると、失敗したURLから再開します。

```bash
python3 scraper.py --fresh
python3 scraper.py --max-pages 10 --output sample.csv --state .sample-state.json
python3 -m unittest -v
```

`--max-pages`は動作確認用です。通常の実行にはページ数制限はありません。取得できなかったURLやrobots.txtで除外したURLは`crawl_report.json`に記録します。CSVはリンクをたどって取得できたHTMLページの一覧であり、リンクのないページやJavaScriptのみで生成されるリンクは含みません。

HTMLからタイトルを取得できなかったページは、タイトルを空欄のままCSVに保存し、レポートの`missing_titles`にも記録します。

## 今回の取得結果（2026年10月23日）

- HTMLページ：2,676件。探索キューが空になるまで実行しました。
- タイトルが空欄のページ：43件。レポートにURLを記録しています。サンプルを確認したところ、元のHTMLも`<title></title>`でした。
- 取得失敗・対象外への転送：141件。内訳はレポートを参照してください。404、認証が必要なページ、別ホストへの転送などが含まれます。
- URLの重複がないこと、CSVと辞書の内容が一致することを確認しました。
- 単体テスト9件を実行し、すべて成功しました。

## Gitでの作業

最初にmainへこのREADMEをコミットし、実装は`feature/sitemap-crawler`ブランチで行います。動作を確認してからmainへマージします。提出時の公開リポジトリ名は`学籍番号-Scraping-task`です。

GitHubに空のPublicリポジトリを作った後、リポジトリのURLを指定してアップロードします。

```bash
git remote add origin https://github.com/自分のユーザー名/学籍番号-Scraping-task.git
git push -u origin main
git push -u origin feature/sitemap-crawler
```

Google Classroomには、このGitHubリポジトリのURLを提出します。
