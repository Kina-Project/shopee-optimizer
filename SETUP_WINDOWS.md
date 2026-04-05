# Shopee Optimizer セットアップ手順（Windows + Claude Code）

このファイルをClaude Codeに読み込ませてください。
Claude Codeが自動でセットアップを進めます。ユーザー操作が必要な場面では指示を出します。

---

## Claude Codeへの指示

あなたはこのツールのセットアップを担当します。以下の手順を**上から順番に**実行してください。

- 自動で解決できるエラーは自分で対処してください
- ユーザーの操作が必要な場合のみ、**具体的に何をしてほしいか**を伝えてください
- 各ステップ完了後、次に進んでください。途中で止まらないでください
- `python` が見つからない場合は `py` も試してください
- **問題が発生して自分で解決できない場合**は、ユーザーに以下のように具体的に情報を求めてください：
  - 「エラーメッセージを貼り付けてください」
  - 「ブラウザの画面のスクリーンショットをください」
  - 「Google Cloud Consoleの○○ページのスクリーンショットをください」
  - 「.envファイルの中身を見せてください（APIキーは伏せてOKです）」
  - 曖昧に「どうなりましたか？」とは聞かず、**何が見たいかを具体的に**伝えてください

---

### Step 1: 前提確認

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

### Step 2: 依存パッケージのインストール

このファイルがあるフォルダ（shopee-optimizer-main）に移動して実行：

```
pip install -r requirements.txt
```

エラーが出た場合は `pip install --upgrade pip setuptools wheel` してから再実行してください。

---

### Step 3: .env ファイルの設定

以前セットアップした `.env` ファイルがPC上にある可能性があります。
まず、デスクトップや以前のshopee-optimizer-mainフォルダ内に `.env` ファイルがないか探してください。

```
dir /s /b C:\Users\%USERNAME%\Desktop\*.env
dir /s /b C:\Users\%USERNAME%\OneDrive\Desktop\*.env
```

- **見つかった場合**: その `.env` をこのフォルダにコピーしてください。APIキーはすでに設定済みです。
- **見つからない場合**: `.env.example` を `.env` にコピーして、ユーザーにAPIキーの入力を依頼してください。

```
copy .env.example .env
```

`.env` が空（APIキーが未設定）の場合、ユーザーに以下を伝えてください：

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

ユーザーがAPIキーをどこかのファイルにまとめている場合は、そのファイルを読んで .env に設定しても構いません。

---

### Step 4: サービスアカウント鍵の配置

このフォルダ内に `keys` フォルダを作成してください。

```
mkdir keys
```

以前セットアップした `service-account-key.json` がPC上にある可能性があります。
まず探してください。

```
dir /s /b C:\Users\%USERNAME%\Desktop\*service-account*.json
dir /s /b C:\Users\%USERNAME%\OneDrive\Desktop\*service-account*.json
dir /s /b C:\Users\%USERNAME%\Downloads\*service-account*.json
```

また、以前のshopee-optimizer-mainフォルダ内の `keys` フォルダも確認してください。

- **見つかった場合**: `keys/service-account-key.json` にコピーしてください。
- **見つからない場合**: ユーザーに以下を伝えてください：

> Google Cloudで作成したサービスアカウントの鍵ファイル（JSON）を、
> `keys/service-account-key.json` として配置してください。
> ファイルがどこにあるか教えてください。こちらでコピーします。

---

### Step 5: 起動

以下のコマンドでサーバーを起動してください。

```
python app.py
```

起動に成功したら、ユーザーに以下を伝えてください：

> サーバーが起動しました！
> ブラウザで http://localhost:8080 を開いてください。
>
> もしWindows Defenderのファイアウォール警告が出たら「アクセスを許可する」を選んでください。

---

### Step 6: Google Drive認証（ユーザー操作が必要）

ユーザーに以下を伝えてください：

> ブラウザで http://localhost:8080 を開くと、画面上部に「Drive 未接続」というボタンがあります。
> これをクリックして、Googleアカウントでログイン・許可してください。
> 「Google Drive 認証完了」と表示されたら成功です。自動的にトップページに戻ります。

もし「Drive 未接続」ボタンから認証できない場合（Google Workspaceの制限等）は、
以下のバックアップ手順を実行してください：

```
python get_token.py
```

ブラウザが開くのでGoogleアカウントでログイン・許可すると、
`DRIVE_REFRESH_TOKEN=xxxxx` が表示されます。
この値を .env の `DRIVE_REFRESH_TOKEN` に設定して、サーバーを再起動してください。

---

### Step 7: 動作確認

ユーザーに以下を伝えてください：

> セットアップ完了です！
> ブラウザで http://localhost:8080 を開いて、AmazonのURLを入力して試してみてください。
> 
> テスト用URL例：
> https://www.amazon.co.jp/dp/B0D7RCZ5GG
> 
> 次回以降は `start_shopee.bat` をダブルクリックするだけで起動できます。

---

## アップデート手順

新しいバージョンのZIPを受け取った場合：

1. 新しいZIPをデスクトップ等に展開
2. 新しいフォルダ内の `update_shopee.bat` をダブルクリック
3. 旧フォルダのパスを入力（例: `C:\Users\admin\Desktop\shopee-optimizer-main`）
4. `.env`、SA鍵、Drive認証トークンが自動でコピーされます
5. `start_shopee.bat` をダブルクリックして起動

**重要: 旧フォルダは更新完了後も念のため残しておいてください。**

### 更新時に自動コピーされるファイル
- `.env`（APIキー設定）
- `keys/service-account-key.json`（Sheets認証鍵）
- `output/_config/drive_token.json`（Drive認証トークン）

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

### スプレッドシートに書き込めない
→ サービスアカウントのメールアドレス（`xxx@xxx.iam.gserviceaccount.com`）がスプレッドシートに「編集者」として共有されているか確認してください。

### `python` コマンドが見つからない
→ `py` コマンドを試してください。または `winget install Python.Python.3.12` でインストール後、ターミナルを再起動してください。

### localhost:8080 がブラウザで開けない
→ サーバーが起動しているか確認してください（ターミナルに「Uvicorn running on...」と表示されているか）。
→ Windows Defenderでブロックされていないか確認してください。
