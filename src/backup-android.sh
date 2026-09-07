#!/bin/sh
# ============================================================
#  Android 目录 -> tar.xz -> 本机文件（流式，手机不落中间文件）
#
#  前置条件：
#    1. 手机装好 Termux，并已 pkg install openssh python
#    2. Termux 里跑过 passwd、termux-wake-lock、sshd
#    3. Termux 已被授予存储权限（termux-setup-storage）
#
#  用法：  ./backup-android.sh
# ============================================================
set -eu

PORT=8022
USER=u0_a123                 # 用 Termux 里 whoami 查
REMOTE_DIR=/sdcard/DCIM
REMOTE_PY=/sdcard/paxck.py
OUT=backup.tar.xz
HERE=$(cd "$(dirname "$0")" && pwd)

PY=$(command -v python3 || command -v python || true)
[ -n "$PY" ] || { echo "[错误] 未找到 python3"; exit 1; }

echo "[1/4] 推送 paxck.py 到手机..."
adb push "$HERE/paxck.py" "$REMOTE_PY"

echo "[2/4] 建立端口转发 tcp:$PORT ..."
adb forward "tcp:$PORT" "tcp:$PORT"

echo "[3/4] 传输中（手机端打包+压缩，数据直接落到本机）..."
# 远端 bash -o pipefail：否则 tar 失败时整条管道仍返回 0（静默失败）
# xz -6：-9 输出与 -6 完全相同却更慢更耗内存；-T0 收益仅约 6%
if ! ssh -p "$PORT" -o Compression=no -o ServerAliveInterval=30 \
      "$USER@localhost" \
      "bash -o pipefail -c 'cd $REMOTE_DIR && python3 $REMOTE_PY create . | xz -6 -c'" \
      > "$OUT"; then
    echo "[错误] 传输失败"
    echo "       常见原因：目录不存在 / 存储权限未授予 / sshd 未启动"
    exit 1
fi

echo "[4/4] 校验归档..."
# paxck 自动识别 xz 流（标准库 lzma），本机无需安装任何压缩工具
if ! "$PY" "$HERE/paxck.py" verify "$OUT"; then
    echo ""
    echo "[失败] 归档校验未通过，请重新传输"
    exit 1
fi

echo ""
echo "[完成] $OUT"
echo "       大小: $(wc -c < "$OUT") 字节"
