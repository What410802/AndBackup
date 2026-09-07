@echo off
REM ============================================================
REM  Android 目录 -> tar.xz -> PC 本地文件（流式，手机不落中间文件）
REM
REM  为什么用 cmd 而不是 PowerShell：
REM    cmd 的 > 重定向是纯字节搬运；PowerShell 会把输出按控制台编码
REM    解码成字符串再编码写文件，压缩流中的随机字节会被替换成 U+FFFD，
REM    导致归档永久损坏（实测约 12% 的字节被污染）。
REM
REM  前置条件：
REM    1. 手机装好 Termux（GitHub 版或任一版本），并已 pkg install openssh
REM    2. Termux 里跑过 pkg install python、passwd、termux-wake-lock、sshd
REM    3. USB 调试已开，adb 与 ssh 在 PATH 中
REM    4. Termux 已被授予存储权限（termux-setup-storage）
REM
REM  用法：  backup-android.bat
REM ============================================================
setlocal

REM ===== 配置区（按实际情况修改）=====
set PORT=8022
set USER=u0_a123
set REMOTE_DIR=/sdcard/DCIM
set REMOTE_PY=/sdcard/paxck.py
set OUT=backup.tar.xz

REM ===== 0. 定位本机 python =====
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
echo [信息] 使用解释器: %PY%

REM ===== 1. 把校验脚本推到手机 =====
if not exist "%~dp0paxck.py" (
    echo [错误] 当前目录缺少 paxck.py
    exit /b 1
)
echo [1/4] 推送 paxck.py 到手机...
adb push "%~dp0paxck.py" %REMOTE_PY% || (
    echo [错误] adb push 失败，检查 USB 调试与设备连接
    exit /b 1
)

REM ===== 2. 建立端口转发 =====
echo [2/4] 建立端口转发 tcp:%PORT% ...
adb forward tcp:%PORT% tcp:%PORT%

REM ===== 3. 流式传输 =====
REM  远端要点：
REM    bash -o pipefail  —— 否则 tar 失败时整条管道仍返回 0（静默失败）
REM    xz -6             —— -9 输出与 -6 完全相同却更慢更耗内存；-T0 收益仅约 6%
echo [3/4] 传输中（手机端打包+压缩，数据直接落到本机）...
ssh -p %PORT% -o Compression=no -o ServerAliveInterval=30 %USER%@localhost "bash -o pipefail -c 'cd %REMOTE_DIR% && python3 %REMOTE_PY% create . | xz -6 -c'" > %OUT%
REM 先把退出码存进变量：在括号块里直接展开 %errorlevel% 会拿到过期的旧值
set RC=%errorlevel%
if not "%RC%"=="0" (
    echo [错误] 传输失败，退出码 %RC%
    echo        常见原因：目录不存在 / 存储权限未授予 / sshd 未启动
    exit /b 1
)

REM ===== 4. 校验 =====
REM  paxck 自动识别 xz 流（标准库 lzma），本机无需安装任何压缩工具。
REM  校验三件事：流是否完整、条目数是否 > 0、每个文件的 SHA-256 是否匹配。
echo [4/4] 校验归档...
%PY% "%~dp0paxck.py" verify %OUT%
if errorlevel 1 (
    echo.
    echo [失败] 归档校验未通过，请重新传输
    exit /b 1
)

echo.
echo [完成] %OUT%
for %%F in (%OUT%) do echo        大小: %%~zF 字节
endlocal
