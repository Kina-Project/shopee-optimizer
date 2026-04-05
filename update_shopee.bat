@echo off
chcp 65001 >nul

echo ========================================
echo  Shopee Optimizer アップデート
echo ========================================
echo.
echo 新しいZIPを展開した後、このファイルを実行してください。
echo 旧フォルダから設定ファイルをコピーします。
echo.

set /p OLD_DIR="旧フォルダのパスを入力してください（例: C:\Users\admin\Desktop\shopee-optimizer-main_old）: "

if not exist "%OLD_DIR%" (
    echo [ERROR] 指定されたフォルダが見つかりません: %OLD_DIR%
    pause
    exit /b 1
)

echo.
echo 以下のファイルをコピーします:

REM .env のコピー
if exist "%OLD_DIR%\.env" (
    copy /Y "%OLD_DIR%\.env" ".env"
    echo   [OK] .env
) else (
    echo   [SKIP] .env（旧フォルダにありません）
)

REM keys フォルダのコピー
if exist "%OLD_DIR%\keys\service-account-key.json" (
    if not exist "keys" mkdir keys
    copy /Y "%OLD_DIR%\keys\service-account-key.json" "keys\service-account-key.json"
    echo   [OK] keys/service-account-key.json
) else (
    echo   [SKIP] keys/service-account-key.json（旧フォルダにありません）
)

REM Drive認証トークンのコピー
if exist "%OLD_DIR%\output\_config\drive_token.json" (
    if not exist "output\_config" mkdir "output\_config"
    copy /Y "%OLD_DIR%\output\_config\drive_token.json" "output\_config\drive_token.json"
    echo   [OK] output/_config/drive_token.json（Drive認証情報）
) else (
    echo   [SKIP] output/_config/drive_token.json（旧フォルダにありません）
)

echo.
echo ========================================
echo  アップデート完了！
echo  start_shopee.bat をダブルクリックして起動してください。
echo ========================================
pause
