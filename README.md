# 鍵メモ（Google Apps Script版）

パスワード付きでテキストを保存・編集できるウェブアプリです。
マイドライブの「鍵メモ」フォルダにファイルを保存し、暗号化と復号はすべてブラウザ側で行います（サーバー側には暗号文しか届きません）。

開発元: [usePocket](https://usepocket.com)

## フォルダ構成

| パス | 内容 |
|---|---|
| `gas/Code.gs` | Apps Script のサーバー側コード |
| `gas/index.html` | 画面（暗号化・復号もここで実行） |
| `guide-site/kagimemo-guide.html` | 公開用セットアップガイド |
| `guide-site/counter.cgi` | ガイドページ用アクセスカウンター（Perl CGI） |
| `guide-site/counter_data/.htaccess` | カウンターデータを外から読めなくする設定 |

## 反映方法

- **アプリ本体**: `gas/` の2ファイルを Apps Script エディタの `コード.gs` と `index.html` に貼り付け、「デプロイ → デプロイを管理」から新しいバージョンとして更新します。
  - デプロイ設定: 種類「ウェブアプリ」／実行ユーザー「自分」／アクセス「自分のみ」
- **ガイドサイト**: `guide-site/` の中身をサーバーにアップロードします。`counter.cgi` は 755、`counter_data` は 755（書き込めなければ 707 か 777）。

## 注意

- `counter.cgi` の `$HASH_SALT` はリポジトリでは初期値のままです。サーバーに置くものは任意の文字列に変えてください。
- カウンターの記録データ（`counter_data/*.dat`）はリポジトリに入れません。
