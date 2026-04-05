# Shopee Optimizer セットアップ（Windows + Claude Code）

このファイルを読んだら、まず**モード判定**を行い、該当するステップをユーザーに一覧表示してください。

---

## Claude Codeへの共通指示

- 自動で解決できるエラーは自分で対処する
- ユーザー操作が必要な場合のみ、**具体的に何をしてほしいか**を伝える
- 各ステップ完了後、次に進む。途中で止まらない
- `python` が見つからない場合は `py` も試す
- **問題が発生して自分で解決できない場合**は、具体的に情報を求める：
  - 「エラーメッセージを貼り付けてください」
  - 「ブラウザの画面のスクリーンショットをください」
  - 「Google Cloud Consoleの○○ページのスクリーンショットをください」
  - 「.envファイルの中身を見せてください（APIキーは伏せてOKです）」
  - 曖昧に「どうなりましたか？」とは聞かず、**何が見たいかを具体的に**伝える

---

## モード判定

まず、このフォルダに `.env` ファイルが**あるかどうか**を確認してください。
また、PC上に以前の `shopee-optimizer-main` フォルダがないかも確認してください。

```
dir /s /b C:\Users\%USERNAME%\Desktop\shopee-optimizer-main*
dir /s /b C:\Users\%USERNAME%\OneDrive\Desktop\shopee-optimizer-main*
```

判定結果に応じて、ユーザーに以下を表示してください：

### A. アップデートの場合（旧フォルダが見つかった）

> **アップデートモードで進めます。以下のステップを実行します：**
>
> 1. 旧フォルダから設定ファイルをコピー
> 2. 依存パッケージの更新
> 3. 起動確認
>
> 旧フォルダの設定をそのまま引き継ぐので、APIキーの再入力は不要です。

→ **「A. アップデート手順」** に進む

### B. 初回セットアップの場合（旧フォルダなし）

> **初回セットアップモードで進めます。以下のステップを実行します：**
>
> 1. Python / ffmpeg のインストール確認
> 2. 依存パッケージのインストール
> 3. APIキーの設定（.env）
> 4. サービスアカウント鍵の配置
> 5. 起動
> 6. Google Drive認証
> 7. 動作確認
>
> 各APIキーの入力が必要です。事前にメモ帳等にまとめておくとスムーズです。

→ **「B. 初回セットアップ手順」** に進む

---

# A. アップデート手順

### A-1: 旧フォルダから設定ファイルをコピー

旧フォルダのパスを特定したら、以下の3ファイルをこのフォルダにコピーしてください。

```
copy "（旧フォルダパス）\.env" ".env"
```

```
mkdir keys 2>nul
copy "（旧フォルダパス）\keys\service-account-key.json" "keys\service-account-key.json"
```

```
mkdir "output\_config" 2>nul
copy "（旧フォルダパス）\output\_config\drive_token.json" "output\_config\drive_token.json"
```

各ファイルが旧フォルダに存在しない場合はスキップしてOKです。

コピーが完了したら、ユーザーに伝えてください：

> 旧フォルダから以下をコピーしました：
> - .env（APIキー設定）✅ or ❌
> - keys/service-account-key.json（Sheets認証鍵）✅ or ❌
> - output/_config/drive_token.json（Drive認証トークン）✅ or ❌

---

### A-2: 依存パッケージの更新

```
pip install -r requirements.txt
```

---

### A-3: 起動確認

```
python app.py
```

起動したら、ユーザーに伝えてください：

> アップデート完了です！
> ブラウザで http://localhost:8080 を開いて動作確認してください。
> 画面上部の「Drive 接続済み」が緑色になっていればOKです。
>
> もし「Drive 未接続」と表示されたら、クリックして再認証してください。
>
> 次回以降は `start_shopee.bat` をダブルクリックするだけで起動できます。
>
> **旧フォルダは念のためしばらく残しておいてください。**

→ 完了

---

# B. 初回セットアップ手順

### B-1: 前提確認

以下のコマンドを実行して、インストール状況を確認してください。

```
python --version
ffmpeg -version
```

- `python` が見つからない場合は `py --version` も試す
- Pythonが入っていない場合: `winget install Python.Python.3.12` を実行
- ffmpegが入っていない場合: `winget install Gyan.FFmpeg` を実行
- インストール後、ターミナルを再起動して再確認

---

### B-2: 依存パッケージのインストール

このファイルがあるフォルダに移動して実行：

```
pip install -r requirements.txt
```

エラーが出た場合は `pip install --upgrade pip setuptools wheel` してから再実行してください。

---

### B-3: .env ファイルの設定（ユーザー操作が必要）

`.env.example` を `.env` にコピー：

```
copy .env.example .env
```

ユーザーがAPIキーをメモ帳等にまとめている場合は、そのファイルを読んで .env に設定しても構いません。

APIキーが未設定の場合、ユーザーに以下を伝えてください：

> .env ファイルをメモ帳で開きます。以下のAPIキーを入力して保存してください。
> 
> 設定が必要な項目：
> - `OPENAI_API_KEY` — OpenAIのAPIキー
> - `FAL_KEY` — fal.aiのAPIキー  
> - `RAINFOREST_API_KEY` — Rainforest APIのキー
> - `SERPER_API_KEY` — Serper.devのキー（任意。なくても動きます）
> - `DRIVE_CLIENT_ID` — Google Cloud OAuthクライアントID
> - `DRIVE_CLIENT_SECRET` — Google Cloud OAuthクライアントシークレット
> - `DRIVE_PARENT_FOLDER_ID` — Google DriveフォルダのURL（そのまま貼り付けOK）
> - `SPREADSHEET_ID` — GoogleスプレッドシートのURL（そのまま貼り付けOK）
> 
> `DRIVE_REFRESH_TOKEN`、`GCP_KEY_PATH`、`PORT` は空のままでOKです。
> 
> 保存したら教えてください。

---

### B-4: サービスアカウント鍵の配置（ユーザー操作が必要な場合あり）

```
mkdir keys
```

ユーザーに以下を伝えてください：

> Google Cloudで作成したサービスアカウントの鍵ファイル（JSON）を、
> `keys/service-account-key.json` として配置してください。
> ファイルがどこにあるか教えてください。こちらでコピーします。

ユーザーがファイルの場所を教えてくれたら、`keys/service-account-key.json` にコピーしてください。

---

### B-5: 起動

```
python app.py
```

起動に成功したら、ユーザーに以下を伝えてください：

> サーバーが起動しました！
> ブラウザで http://localhost:8080 を開いてください。
>
> もしWindows Defenderのファイアウォール警告が出たら「アクセスを許可する」を選んでください。

---

### B-6: Google Drive認証（ユーザー操作が必要）

ユーザーに以下を伝えてください：

> ブラウザで http://localhost:8080 を開くと、画面上部に「Drive 未接続」というボタンがあります。
> これをクリックして、Googleアカウントでログイン・許可してください。
> 「Google Drive 認証完了」と表示されたら成功です。自動的にトップページに戻ります。

もし認証できない場合（Google Workspaceの制限等）は、バックアップ手順を実行：

```
python get_token.py
```

ブラウザが開くのでGoogleアカウントでログイン・許可すると、
`DRIVE_REFRESH_TOKEN=xxxxx` が表示されます。
この値を .env の `DRIVE_REFRESH_TOKEN` に設定して、サーバーを再起動してください。

---

### B-7: 動作確認

ユーザーに以下を伝えてください：

> セットアップ完了です！
> ブラウザで http://localhost:8080 を開いて、AmazonのURLを入力して試してみてください。
> 
> テスト用URL例：
> https://www.amazon.co.jp/dp/B0D7RCZ5GG
> 
> 次回以降は `start_shopee.bat` をダブルクリックするだけで起動できます。

→ 完了

---

## トラブルシューティング

### 「Rainforest APIキーが必要です」エラー
→ .env ファイルが正しく読み込まれていない可能性があります。
→ app.py の先頭に `from dotenv import load_dotenv` と `load_dotenv()` があるか確認してください。

### ポート8080が使用中
→ .env の `PORT=8080` を `PORT=9090` 等に変更して再起動してください。

### pip install でエラー
→ `pip install --upgrade pip setuptools wheel` を実行してから再試行してください。

### Drive認証で「アクセスをブロック」と表示される
→ Google Cloud ConsoleのOAuth同意画面で、テストユーザーに使用するGoogleアカウントのメールアドレスを追加してください。
→ スクリーンショットを送ってもらってください。

### スプレッドシートに書き込めない
→ サービスアカウントのメールアドレス（`xxx@xxx.iam.gserviceaccount.com`）がスプレッドシートに「編集者」として共有されているか確認してください。

### `python` コマンドが見つからない
→ `py` コマンドを試してください。または `winget install Python.Python.3.12` でインストール後、ターミナルを再起動してください。

### localhost:8080 がブラウザで開けない
→ サーバーが起動しているか確認してください（ターミナルに「Uvicorn running on...」と表示されているか）。
→ Windows Defenderでブロックされていないか確認してください。
