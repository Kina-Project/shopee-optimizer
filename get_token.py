"""
Google Drive OAuth2 リフレッシュトークン取得スクリプト

使い方:
  python get_token.py

事前に .env に DRIVE_CLIENT_ID と DRIVE_CLIENT_SECRET を設定しておくこと。
ブラウザが開くのでGoogleアカウントでログイン・許可する。
表示されたリフレッシュトークンを .env の DRIVE_REFRESH_TOKEN に設定する。
"""

from dotenv import load_dotenv
load_dotenv()

import os
from google_auth_oauthlib.flow import InstalledAppFlow

client_id = os.environ.get("DRIVE_CLIENT_ID", "")
client_secret = os.environ.get("DRIVE_CLIENT_SECRET", "")

if not client_id or not client_secret:
    print("エラー: .env に DRIVE_CLIENT_ID と DRIVE_CLIENT_SECRET を設定してください。")
    exit(1)

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    },
    scopes=["https://www.googleapis.com/auth/drive"],
)

creds = flow.run_local_server(port=0)
print(f"\n{'='*50}")
print(f"DRIVE_REFRESH_TOKEN={creds.refresh_token}")
print(f"{'='*50}")
print("\nこの値を .env の DRIVE_REFRESH_TOKEN に設定してください。")
