# Shopee Optimizer セットアップ手順（Windows + Claude Code）

このファイルをClaude Codeに読み込ませてください。
Claude Codeが自動でセットアップを進めます。ユーザー操作が必要な場面では指示を出します。

---

## Claude Codeへの指示

あなたはこのツールのセットアップを担当します。以下の手順を**上から順番に**実行してください。

- 自動で解決できるエラーは自分で対処してください
- ユーザーの操作が必要な場合のみ、**具体的に何をしてほしいか**を伝えてください
- 各ステップ完了後、次に進んでください。途中で止まらないでください

---

### Step 0: 前提確認

以下のコマンドを実行して、インストール状況を確認してください。

```
python --version
ffmpeg -version
```

- Pythonが入っていない場合: `winget install Python.Python.3.12` を実行
- ffmpegが入っていない場合: `winget install Gyan.FFmpeg` を実行
- インストール後、ターミナルを再起動して再確認

---

### Step 1: 依存パッケージのインストール

このファイルがあるフォルダ（shopee-optimizer-main）に移動して実行：

```
pip install -r requirements.txt
```

エラーが出た場合は `pip install --upgrade pip` してから再実行してください。

---

### Step 2: .env ファイルの作成

`.env.example` を `.env` にコピーしてください。

```
copy .env.example .env
```

---

### Step 3: APIキーの設定（ユーザー操作が必要）

ここでユーザーに以下を伝えてください：

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
> `DRIVE_REFRESH_TOKEN` と `PORT` は空のままでOKです。
> 
> 保存したら教えてください。

ユーザーがAPIキーをどこかのファイルにまとめている場合は、そのファイルを読んで .env に設定しても構いません。

---

### Step 4: サービスアカウント鍵の配置（ユーザー操作が必要な場合あり）

このフォルダ内に `keys` フォルダを作成してください。

```
mkdir keys
```

ユーザーに以下を伝えてください：

> Google Cloudで作成したサービスアカウントの鍵ファイル（JSON）を、
> `keys/service-account-key.json` として配置してください。
> 
> ファイルがデスクトップなどにある場合は、ファイル名を教えてください。
> こちらでコピーします。

ユーザーがファイルの場所を教えてくれたら、`keys/service-account-key.json` にコピーしてください。

---

### Step 5: 起動

以下のコマンドでサーバーを起動してください。

```
python -m uvicorn app:app --host 0.0.0.0 --port 8080
```

起動に成功したら、ユーザーに以下を伝えてください：

> サーバーが起動しました！
> ブラウザで http://localhost:8080 を開いてください。

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
