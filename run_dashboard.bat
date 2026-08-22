@echo off
cd /d "%~dp0"

set "GIT_USER_NAME=Athicha Dashboard Bot"
set "GIT_USER_EMAIL=athicha-dashboard@local"

set "SQLSERVER_HOST=192.168.1.54"
set "SQLSERVER_DB=ATC"
set "SQLSERVER_USER=aticha"
set "SQLSERVER_PASSWORD=sales2011"

python generate_dashboard.py
if errorlevel 1 (
    echo Dashboard generation failed.
    exit /b %errorlevel%
)

git config user.name "%GIT_USER_NAME%"
git config user.email "%GIT_USER_EMAIL%"

git add -A

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Nightly dashboard refresh"
) else (
    echo No changes to commit.
    exit /b 0
)

git push origin main
if errorlevel 1 (
    echo Push failed.
    exit /b %errorlevel%
)

echo Dashboard updated and pushed successfully.
