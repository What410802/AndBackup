@echo off
REM Android 目录 -> tar.xz -> PC 本机文件（设备端不落中间文件）。
REM
REM 只使用 adb exec-out 的 shell 用户读取文件。这是 Android 11+ 读取其他应用
REM Android/data 的可行路径；Termux 等普通应用受 scoped storage 限制，不能承担此事。
REM tar、SHA-256 与 xz 压缩均在本机 Python 中流式完成。
REM
REM 重要：请从 cmd.exe 运行，不能用 PowerShell 的 > 重定向接二进制流。

setlocal

REM ===== 配置区 =====
set ADB=adb
set SOURCE_DIR=/storage/emulated/0/Android/data/com.example.backup/测试.d
set OUT=backup.tar.xz
set COMPRESS=xz
set TMP_OUT=%OUT%.partial

set PAXCK=%~dp0paxck.py
set PY=
for %%P in (python python3 py) do (
    if not defined PY (
        %%P -c "import sys" >nul 2>&1
        if not errorlevel 1 set PY=%%P
    )
)
if not defined PY (
    echo [错误] 未找到 python / python3 / py，请先安装 Python 3 并加入 PATH
    exit /b 1
)
if not exist "%PAXCK%" (
    echo [错误] 缺少 %PAXCK%
    exit /b 1
)

echo [1/3] 检查 ADB 与源目录...
%ADB% get-state >nul 2>&1
if errorlevel 1 (
    echo [错误] adb 不可用，检查 USB 调试授权和 ADB 路径
    exit /b 1
)

echo [2/3] 传输中（adb 直读 -^> 本机端 tar + 压缩）...
%PY% "%PAXCK%" backup-adb --adb "%ADB%" --compress %COMPRESS% "%SOURCE_DIR%" > "%TMP_OUT%"
set RC=%errorlevel%
if not "%RC%"=="0" (
    echo [错误] 传输失败，退出码 %RC%
    echo        常见原因：路径不存在 / adb shell 无读取权限 / 文件在备份中变化
    exit /b 1
)

echo [3/3] 校验 %TMP_OUT% ...
%PY% "%PAXCK%" verify "%TMP_OUT%"
if errorlevel 1 (
    echo [失败] 归档校验未通过，请重新传输
    exit /b 1
)

move /y "%TMP_OUT%" "%OUT%" >nul
if errorlevel 1 (
    echo [错误] 无法将已校验归档移动到 %OUT%
    exit /b 1
)

echo [完成] %OUT%
for %%F in ("%OUT%") do echo        大小: %%~zF 字节
endlocal
