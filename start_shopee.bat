@echo off
chcp 65001 >nul

if not exist ".env" (
    echo [ERROR] .env ファイルが見つかりません。
    echo .env.example をコピーして .env を作成し、APIキーを設定してください。
    echo   copy .env.example .env
    pause
    exit /b 1
)

echo ========================================
echo  Shopee Optimizer 起動中...
echo ========================================

python app.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] 起動に失敗しました。
    echo エラーメッセージを確認してください。
    pause
)
