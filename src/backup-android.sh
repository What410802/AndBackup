#!/bin/sh
# Android 目录 -> tar.<xz|gz|zst> -> 本机文件。
#
# 只走 adb exec-out：Android/data 在 Android 11+ 无法由 Termux 等普通应用读取，
# 而 USB 调试的 shell 用户可直接读取。手机上不会生成 tar、压缩包或临时文件。
#
# 依赖：本机 adb 与 Python 3。xz/gzip 使用 Python 标准库；zstd 需 Python 3.14+
# 或本机 PATH 里的 zstd。设备端仅使用 find、stat、cat 来枚举和读取源文件。
set -eu

ADB="${ADB:-adb}"
SOURCE_DIR="${SOURCE_DIR:-/sdcard/DCIM}"
COMPRESS="${COMPRESS:-xz}"
OUT="${OUT:-}"

case "$0" in
    */*) SCRIPT_DIR=${0%/*} ;;
    *)   SCRIPT_DIR=. ;;
esac
HERE=$(cd "$SCRIPT_DIR" && pwd)
PAXCK="$HERE/paxck.py"
PY=$(command -v python3 || command -v python || true)

[ -n "$PY" ] || { echo "[错误] 未找到 python3"; exit 1; }
[ -f "$PAXCK" ] || { echo "[错误] 缺少 $PAXCK"; exit 1; }

case "$COMPRESS" in
    xz)   EXT=xz ;;
    gzip) EXT=gz ;;
    zstd) EXT=zst ;;
    none) EXT=tar ;;
    *) echo "[错误] 未知压缩类型：$COMPRESS（可选 xz / gzip / zstd / none）"; exit 1 ;;
esac

if [ -z "$OUT" ]; then
    case "$EXT" in
        tar) OUT=backup.tar ;;
        *) OUT="backup.tar.$EXT" ;;
    esac
fi

TMP_OUT="${OUT}.partial.$$"
cleanup() {
    rm -f "$TMP_OUT"
}
trap cleanup EXIT HUP INT TERM

echo "[1/3] 检查 ADB 与源目录..."
"$ADB" get-state >/dev/null || {
    echo "[错误] adb 不可用，检查 USB 调试授权和 ADB 路径"
    exit 1
}

echo "[2/3] 传输中（adb 直读 -> 主机端 tar + 压缩）..."
if ! "$PY" "$PAXCK" backup-adb --adb "$ADB" --compress "$COMPRESS" \
    "$SOURCE_DIR" > "$TMP_OUT"; then
    echo "[错误] 传输失败"
    echo "       常见原因：路径不存在 / adb shell 无读取权限 / 文件在备份中变化"
    exit 1
fi

echo "[3/3] 校验 $TMP_OUT ..."
if ! "$PY" "$PAXCK" verify "$TMP_OUT"; then
    echo "[失败] 归档校验未通过，请重新传输"
    exit 1
fi

mv -f "$TMP_OUT" "$OUT"
trap - EXIT HUP INT TERM

echo "[完成] $OUT"
echo "       大小: $(wc -c < "$OUT") 字节"
