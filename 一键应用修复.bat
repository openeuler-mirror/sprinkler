@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  Sprinkler 一键应用修复
rem  用法：把本文件和 fix_sprinkler.py 放在同一个目录，双击运行
rem  只想预览不改动：一键应用修复.bat --dry-run
rem ============================================================

set "REPO=D:\sprinkler"

if not exist "%REPO%\sprinkler\auto\auto.py" (
    echo.
    echo [错误] 没找到 "%REPO%\sprinkler\auto\auto.py"
    echo        如果仓库不在 D:\sprinkler，请用记事本打开本文件，
    echo        把上面那行 REPO 的值改成实际路径后保存。
    echo.
    pause
    exit /b 1
)

set "PY=python"
where python >nul 2>&1
if errorlevel 1 (
    where py >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [错误] 没找到 python 命令。请安装 Python 3.10+ 并勾选 Add to PATH。
        echo.
        pause
        exit /b 1
    )
    set "PY=py"
)

set "SCRIPT="
if exist "%~dp0fix_sprinkler.py" (
    copy /Y "%~dp0fix_sprinkler.py" "%REPO%\fix_sprinkler.py" >nul
    set "SCRIPT=%REPO%\fix_sprinkler.py"
)
if not defined SCRIPT if exist "%REPO%\fix_sprinkler.py" set "SCRIPT=%REPO%\fix_sprinkler.py"
if not defined SCRIPT (
    echo.
    echo [错误] 没找到 fix_sprinkler.py，请把它和本文件放在同一个目录。
    echo.
    pause
    exit /b 1
)

cd /d "%REPO%"

echo ============================================================
echo  目标仓库 : %REPO%
echo  Python   : %PY%
echo  附加参数 : %*
echo ============================================================
echo.

"%PY%" "%SCRIPT%" --root "%REPO%" %*
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
if "%RC%"=="0" (
    echo  执行完毕。原始文件已备份到 %REPO%\.fix_backup_[时间戳]\
    echo  需要整体回退时，把备份目录里的文件复制回原位即可。
) else (
    echo  执行返回码 %RC%，请把上面的输出发我。
)
echo ------------------------------------------------------------
echo.
pause
endlocal
