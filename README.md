# Portal + SearXNG — Wikipedia概要・番号付きページ送り

PortalとSearXNGを一つのRenderサービスで動かします。SearXNGは内部の127.0.0.1:8888だけで待ち受け、外部へ公開するのはPortalです。SearXNGの登録、Brave/GoogleのAPIキーは不要です。

検索時のURLは `/`、タブ名は `Portal` で固定。検索・ページ送りともPOST。JavaScript有効時は画面だけ更新し、検索ごとの履歴項目を増やしません。

## 今回の更新内容

- 「すべて」の検索結果の右側に、日本語Wikipediaの関連する記事の概要・記事名・出典・続きを読むリンク。スマホでは結果の上に表示します。概要は別のPOST通信で取得し、検索結果を待たせません。記事がない場合や通信失敗時はその旨を表示します。関連度が最も高い記事を使うため、入力の意味と異なる記事が選ばれる場合があります。概要表示にはJavaScriptが必要です。
- Google風の「1・2・3・4・5」と前へ・次へ。現在の番号を強調し、最大10ページ内で番号を切り替えます。番号を押しても検索語はURLやタブ名に入りません。
- 検索元の応答待ちを標準6秒→3.5秒、最大10秒→5秒に短縮。遅い検索元の結果が省かれる場合があります。Portal側は検索8秒、Wikipedia4秒の読み取りタイムアウトです。接続・混雑・Renderの起動時間を含む実際の所要時間を保証する値ではありません。
- 同じ検索語・カテゴリ・セーフサーチ・ページの再取得は、有効期限30秒のメモリキャッシュで減らします。障害や検索元の部分失敗はキャッシュしません。Wikipediaは有効期限5分のサーバーキャッシュと、現在の検索語だけの画面内キャッシュを使います。
- 公開側の処理スレッドを4→8にし、画像や概要の通信が検索処理を占有しにくくしています。
- Chromeの新しいタブとGoogle検索に近い、丸い検索欄・上部検索バー・カテゴリタブ。
- 「すべて」「画像」「動画」「ニュース」。画像はタイルとプレビュー、動画はサムネイル・再生時間・リンクを表示します。動画の再生はリンク先のサイトで行います。
- 右上の歯車からセーフサーチ「オフ・標準・厳格」（初期値は標準）。カテゴリ切り替えとページ送りでも引き継ぎます。対応する検索元に適用する設定なので、完全な除外の保証はありません。
- ライト・ダーク・システムに合わせる外観設定。設定は画面を開いている間だけ保持し、CookieやlocalStorageには保存しません。
- 検索後も検索欄に入力を残して編集できます。URLとタブ名には入れません。「画面をクリア」で入力・結果・プレビューを消します。
- サムネイルはサーバー経由で取得し、ブラウザから画像配信元へ直接接続しません。ランダムな /media/ID を使い、画像のURLや検索語をパラメーターに載せません。

## すでにSearXNG版をGitHubに入れた場合

次のファイルを新版に差し替えます。

| 差し替える | 新しく追加する |
|---|---|
| app.py | cache.py |
| searxng-settings.yml | templates/overview.html |
| gunicorn.conf.py | tests/test_cache.py（テストを置く場合） |
| templates/index.html | |
| templates/results.html | |
| static/app.js | |
| static/style.css | |
| tests/test_app.py（テストを置く場合） | |
| README.md | |

Dockerfile、start.py、render.yamlなどは共通です。ZIP一式を上書きアップロードしても構いません。templatesとstaticのファイルは既存の同名フォルダ内へ入れ、二重にしないでください。

GitHubでCommit changes → Renderで対象サービスの Manual Deploy → Deploy latest commit。サービスがまだない場合は、以下の初回公開手順を使います。

## GitHubに入れる

1. ZIPを展開し、`portal-searxng` フォルダの**中身**を、既存リポジトリ（例: portal-new）の直下へアップロードします。
2. 同じ名前のファイルは新版で上書きし、Commit changesで保存します。
3. `templates` と `static` は既存の同名フォルダの中身を更新します。`templates/templates` にしないでください。
4. リポジトリ直下に app.py、Dockerfile、render.yaml があることを確認します。

差し替えるもの:
- app.py
- render.yaml
- templates/index.html
- templates/results.html
- static/style.css
- static/app.js
- searxng-settings.yml
- README.md
- tests/test_app.py（テストを置いている場合）

新しく追加するもの:
- media.py
- Dockerfile（大文字D、拡張子なし）
- start.py
- searxng_backend.py
- searxng-gunicorn.conf.py
- searxng-settings.yml
- .dockerignore
- LICENSE

requirements.txt、.gitignoreは共通。今回の更新ではgunicorn.conf.pyも差し替えてください。cache.pyとtemplates/overview.htmlは新しく追加します。ZIPには全部入っているので、一式アップロードしても構いません。

## Renderで公開する

1. https://dashboard.render.com/ の New → Blueprint。
2. GitHubを接続し、更新したリポジトリをConnect。
3. Blueprint Nameは portal など任意、Branchは main、Blueprint Pathは render.yaml。
4. サービス名 portal-searxng、Runtime Docker、Plan Free を確認。
5. Deploy Blueprintを押します。APIキー入力は不要です。
6. 公開完了後、portal-searxngサービスの https://….onrender.com を開きます。

古いBrave APIキー入力欄が残る場合は、新render.yamlをCommitしてから、Blueprint作成画面を開き直して接続してください。

旧Python版をすでに公開した場合は別のDockerサービスを作ります。旧サービスは自動削除されません。新サイトの動作確認後、不要なら旧サービスを削除してください。

Blueprintを使わない場合は New → Web Service で同じリポジトリを接続し、次を設定します。

| 項目 | 値 |
|---|---|
| Name | portal-searxng |
| Runtime / Language | Docker |
| Root Directory | 空欄（直下にファイルを置いた場合） |
| Dockerfile Path | ./Dockerfile |
| Docker Command | 空欄（DockerfileのCMDを使用） |
| Plan | Free |
| Health Check Path | /health |
| APIキー | 不要 |

## SearXNGの使い方

公開されたPortalで検索するだけです。SearXNGも自動起動するので、別のSearXNGサイトへの登録・ログイン・接続先入力は不要。

Google、Bing、DuckDuckGo、BraveのWeb・画像・動画・ニュース検索（計16エンジン）を有効にしています。BraveはWeb検索元として使い、Brave Search APIは使いません。SearXNGのJSON出力も設定済みです。

検索元を停止する場合は、searxng-settings.yml末尾の該当エンジンのdisabledをtrueにします。次の例は一般Web検索のGoogleとBingを停止する設定です。画像・動画・ニュースは、google images、google videos、google newsなどを個別に変更します。

```yaml
engines:
  - name: duckduckgo
    disabled: false
  - name: brave
    disabled: false
  - name: google
    disabled: true
  - name: bing
    disabled: true
```

変更はGitHubでCommitしてRenderで再デプロイ。自動反映されない場合はサービスの Manual Deploy → Deploy latest commit。

search.formatsのjsonを削除すると結果を取得できなくなります。内部SearXNGのlimiterは無効です。PortalからSearXNGへの新規検索には毎分30回の全体制限を設け、利用者のIPを保存せず利用回数だけを管理しています。結果キャッシュの再表示は新規検索に含みません。Wikipediaへの概要取得は同時2件まで。利用者ごとの制限・ログインはなく、URLを知る人は使えます。

## 公開後の確認

- サイトが開き、検索結果リンクが表示される。
- URLに検索語が出ず、タブ名がPortalのまま。
- ページ送りでも検索語がURLに出ない。
- 「画面をクリア」で入力・結果・ページ送り用の検索語を消せる。

最大10ページ。SearXNGのJSONには総ページ数や確実な次ページの有無がないため、番号は総ページ数を示すものではありません。結果がある間は最大5個の番号と「次へ」を表示し、現在のページの前後へ移動できます。移動先が空になる場合があります。空のページでは先の番号と「次へ」を表示せず、前のページへ戻れます。

RenderのFreeサービスはアクセスが15分ないと休止し、次回アクセスで再起動するまで約1分かかる場合があります。この初回起動待ちは検索のタイムアウトやキャッシュでは解消しません。起動後の検索の待ち時間を減らす変更です。公式説明: https://render.com/docs/free

## 困ったとき

| 状態 | 対処 |
|---|---|
| Brave APIキーを求められる | GitHubの旧render.yamlを新版で上書きし、作成画面を開き直す |
| TemplateNotFound | templates/index.html と templates/results.html の位置を確認 |
| Dockerfileが見つからない | 直下にDockerfileがあり、.txtなどが付いていないか確認 |
| 起動失敗 | RenderのLogsで依存関係・設定の起動エラーを確認 |
| 検索元が応答しない | CAPTCHA・アクセス制限・通信失敗の可能性。少し待ち、利用可能な検索元を使う |
| 開くまで時間がかかる | Freeサービスの休止からの起動待ちの可能性 |
| メモリ不足 | Free枠に収まる保証はない。検索元を減らすか、必要ならプランを見直す |

検索元がRenderの接続を拒否する場合があります。公式APIと同じ安定性は保証しません。

## プライバシー

- Portalは検索履歴DB、Cookie、localStorage、広告、解析、外部フォントを使いません。画像はPortalサーバーが取得して表示します。ブラウザから外部画像を直接読み込みません。
- Portal/SearXNGのアクセスログ・検索処理ログを出さず、検索元の生エラーも画面に出しません。
- SearXNGはエンジン動作用の一時キャッシュを使います。画像取得先のURLとランダムIDはサーバーのメモリで最大10分・600件まで保持し、ファイルへ保存しません。画像URL自体に検索語が含まれる場合があります。
- Portalの結果キャッシュは有効期限30秒・最大32件・合計2MB、Wikipediaキャッシュは5分・最大64件・合計512KB。キーは検索条件のSHA-256で、検索結果や概要の本文は一時的にメモリへ保持します。期限切れの項目は次のキャッシュアクセス時に削除し、再起動でも消えます。利用者との関連付けやファイルへの保存は行いません。
- 入力とページ送り用の検索語は、結果表示中のDOM・メモリにあります。「画面をクリア」で削除します。
- POST、no-store、no-referrer、戻る操作のページ復元時のクリアを設定。端末・ブラウザ・拡張機能・ホスティング事業者側の保存は完全には制御できません。
- 検索語はRender上のPortal/SearXNGと、有効な検索サービスに届きます。利用者のIP・Cookie・ブラウザのUser-Agentを検索元に転送しませんが、Renderサーバーからの接続は見えます。
- 「すべて」では概要を探すため、検索語がPortalサーバーから日本語Wikipediaにも送られます。ブラウザはWikipediaへ直接接続しません。Wikipediaの記事リンクを開く際は通常の外部サイトへの接続になります。
- URLとタイトルから検索語が分からない設計で、サイトへの訪問自体や画面の結果を隠す保証はありません。HTTPSでアクセスしてください。
- 外部リンクは通常の直接接続です。訪問先のURL・タイトルが表示されます。匿名ビュー機能はありません。

## ローカル起動

DockerがあるPCで:

```bash
docker build -t portal-searxng .
docker run --rm -p 10000:10000 portal-searxng
```

http://localhost:10000/ を開きます。

固定したSearXNGコミット: 6a0ac6a39112d4a73a80eda4564d68ba090a9eff。

## 検証済みの範囲

アプリ・画像プロキシ・キャッシュの23項目のテストは通過。固定したSearXNGソースと同じ依存関係で、2サーバーの起動・内部接続・ヘルスチェック・16検索元の設定読込を確認しました。ブラウザでも、テスト用検索結果と概要を使って、Wikipediaの配置・概要通信中も検索結果を操作できること・ページ切り替えで概要を再取得しないこと・クリア後に遅れた概要が戻らないこと・番号による3ページ目への移動・カテゴリ・セーフサーチの維持・画像プレビュー・画面クリア・テーマ・固定URL/タイトル・履歴項目が増えないこと・Cookieなし・スマホ幅の表示を確認しました。JavaScript無効時のフォーム検索と、大きい画像を取得できない場合のサムネイルへの復帰も確認しました。実際の外部検索結果やWikipediaを取得した検証ではなく、実運用の速度測定も未実施です。

検証環境からは外部検索結果を取得できなかったため、結果取得はRenderで公開後に確認が必要。この環境にはDockerがないためDockerイメージのビルド自体とRender公開は未実行です。

```bash
python -m unittest discover -s tests -v
```

### 画像プロキシの範囲

サムネイル・プレビューはPNG/JPEG/GIF/WebP/AVIF、最大2MB、3回までの接続先確認付きリクエスト、同時4件。SVGやHTML、非公開IP、ローカルネットワーク、メタデータ用IPへは接続しません。DNSで確認した公開IPへ接続を固定し、リダイレクト先も検証します。画像の元サイトが拒否した場合や期限切れではプレビューを表示できません。大きい画像のプレビューが失敗した場合はサムネイルに戻します。

## ライセンス・ソース

Portal一式はAGPL-3.0-or-later（LICENSE参照）。SearXNGの著作権・ライセンス表示はそのソースに保持します。公開サイトの「ソース」から、この構成と固定したSearXNGのソースを取得できます。秘密鍵は起動時に生成し、ソースアーカイブには入れません。

Wikipediaの概要本文はソフトウェアのライセンスとは別です。記事への出典リンクとCC BY-SA 4.0へのリンクを表示します。本文は冒頭の短い抜粋として表示し、記事全文はWikipediaで読めます。

公式資料:
- https://docs.searxng.org/dev/search_api.html
- https://docs.searxng.org/admin/settings/settings_server.html
- https://render.com/docs/docker
- https://render.com/docs/infrastructure-as-code
- https://render.com/docs/free
- https://www.mediawiki.org/wiki/Extension:TextExtracts
- https://www.mediawiki.org/wiki/API:Search
- https://docs.searxng.org/admin/settings/settings_outgoing.html
