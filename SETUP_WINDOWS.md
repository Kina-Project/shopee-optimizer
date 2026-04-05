# Shopee Optimizer セットアップ（Windows + Claude Code）

このファイルを読んだら、まずユーザーに**全体の流れ**を表示してから進めてください。

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

## Step 1: ZIPの展開（ユーザー操作が必要）

まず、ユーザーに以下を伝えてください：

> **Shopee Optimizerのアップデートを始めます！以下の4ステップで完了します：**
>
> 1. ZIPの展開
> 2. 旧フォルダから設定ファイルをコピー
> 3. 依存パッケージの更新
> 4. 起動確認
>
> APIキーの再入力は不要です。旧フォルダの設定をそのまま引き継ぎます。
>
> ---
>
> **まず、受け取ったZIPファイルをデスクトップに展開してください。**
>
> ⚠️ **重要：ZIPの展開について**
> - ZIPを展開すると `shopee-optimizer-main` というフォルダが作成されます
> - 以前のフォルダが同じ場所にある場合、**上書きされて設定が消えてしまいます**
> - 以前のフォルダがある場合は、**先にフォルダ名を変更**してください
>   - 例: `shopee-optimizer-main` → `shopee-optimizer-main_old`
> - その後、ZIPを展開してください
>
> 展開が終わったら教えてください。

ユーザーが展開完了を報告したら、新しいフォルダに移動して次に進んでください。

---

## Step 2: 旧フォルダから設定ファイルをコピー

PC上にある旧フォルダ（リネーム済みのものを含む）を探してください。

```
dir /s /b C:\Users\%USERNAME%\Desktop\shopee-optimizer-main*
dir /s /b C:\Users\%USERNAME%\OneDrive\Desktop\shopee-optimizer-main*
```

旧フォルダが見つかったら、以下の3ファイルを新しいフォルダにコピーしてください。

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
>
> ❌のものがあっても、後で設定できるので大丈夫です。

---

## Step 3: 依存パッケージの更新

```
pip install -r requirements.txt
```

エラーが出た場合は `pip install --upgrade pip setuptools wheel` してから再実行してください。

---

## Step 4: 起動確認

```
python app.py
```

起動に成功したら、ユーザーに以下を伝えてください：

> アップデート完了です！
> ブラウザで http://localhost:8080 を開いて動作確認してください。
>
> 確認ポイント：
> - 画面上部の「Drive 接続済み」が緑色になっていればOKです
> - もし「Drive 未接続」と表示されたら、クリックしてGoogleアカウントで再認証してください
> - もしWindows Defenderのファイアウォール警告が出たら「アクセスを許可する」を選んでください
>
> 次回以降は `start_shopee.bat` をダブルクリックするだけで起動できます。
>
> **旧フォルダは念のためしばらく残しておいてください。**

→ 完了

---

## トラブルシューティング

### 「Rainforest APIキーが必要です」エラー
→ .env ファイルが正しく読み込まれていない可能性があります。
→ .env ファイルの中身を確認してください。RAINFOREST_API_KEY に値が入っているか見せてください。

### ポート8080が使用中
→ .env の `PORT=8080` を `PORT=9090` 等に変更して再起動してください。

### pip install でエラー
→ `pip install --upgrade pip setuptools wheel` を実行してから再試行してください。

### Drive認証で「アクセスをブロック」と表示される
→ Google Cloud ConsoleのOAuth同意画面で、テストユーザーに使用するGoogleアカウントのメールアドレスを追加してください。
→ その画面のスクリーンショットを送ってもらってください。

### Drive認証ボタンが機能しない場合のバックアップ手順
→ 以下のコマンドを実行してください：
```
python get_token.py
```
→ ブラウザが開くのでGoogleアカウントでログイン・許可すると、`DRIVE_REFRESH_TOKEN=xxxxx` が表示されます。
→ この値を .env の `DRIVE_REFRESH_TOKEN` に設定して、サーバーを再起動してください。

### スプレッドシートに書き込めない
→ サービスアカウントのメールアドレス（`xxx@xxx.iam.gserviceaccount.com`）がスプレッドシートに「編集者」として共有されているか確認してください。

### `python` コマンドが見つからない
→ `py` コマンドを試してください。または `winget install Python.Python.3.12` でインストール後、ターミナルを再起動してください。

### localhost:8080 がブラウザで開けない
→ サーバーが起動しているか確認してください（ターミナルに「Uvicorn running on...」と表示されているか）。
→ Windows Defenderでブロックされていないか確認してください。
