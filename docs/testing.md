# 测试说明

测试分为三层：不依赖设备的 Python 单元测试、用替身 ADB 跑真实 Linux 主控的离线集成测试，
以及连接 Android 设备后的真机集成测试。Windows/CMD 当前只做代码与人工审查，按约定暂不
接入自动化执行。

## 运行

```sh
# 标准库即可
python3 -m unittest discover -s tests -t . -v

# 可选 pytest
python3 -m pytest tests -q
```

真机测试会自动查找 `adb`；也可指定：

```sh
ANDROBACKUP_ADB=/path/to/platform-tools/adb \
python3 -m unittest -v tests.test_device_integration
```

没有已授权设备时，真机测试会 `skip`，不会使离线 CI 失败。

## 覆盖矩阵

```mermaid
flowchart LR
    U[单元测试\n纯 Python] --> I[离线集成\n替身 adb + 真实 .sh]
    I --> D[真机集成\n真实 adb + Android 15]
    U --> V[校验器\n退出码与损坏归档]
    I --> B[主控脚本\n路径/压缩/失败清理]
    D --> P[用户目标路径\nAndroid/data]
```

| 文件 | 类型 | 重点 |
|---|---|---|
| `tests/test_paxck_unit.py` | 单元 | 魔术字节、流读取器、PAX 哈希、符号链接、硬链接、变化文件、压缩和校验退出码 |
| `tests/test_pipeline_local.py` | 离线集成 | `create | compress | verify`、系统 tar 互操作、还原保真、非 UTF-8 文件名 |
| `tests/test_backup_sh_integration.py` | 离线集成 | 真实 `.sh` + 替身 ADB；中文/空格/单引号路径、二进制安全、截断、错误退出、Android/data 路径 |
| `tests/test_device_integration.py` | 真机集成 | ADB 授权、目标目录读取、双遍字节一致性、真实脚本备份、设备空间不生成中间文件 |

## 单元测试要点

`paxck.py create` 的样例树包含普通文件、空文件、跨 1 MiB 边界的大文件、中文和空格名、
指向文件/目录的符号链接、断链以及 FIFO。测试确认：

- PAX 扩展头中的 SHA-256 与归档内容一致。
- 指向目录的符号链接保持 `SYMTYPE`，不会被 `os.walk` 错写成空目录。
- 文件在两遍读取间被删除、缩短或权限改变时不会产出“成功”的坏归档。
- ADB/管道的合法短读不会被误判为 EOF。
- 归档缺条目、截断、内容翻转或没有任何 SHA-256 记录时，`verify` 返回 1。

## 离线主控集成

替身 ADB 在本机临时目录上实现 `get-state` 与 `exec-out sh -c ...`，真实执行 find/stat/
readlink/cat。这样不依赖手机，也能验证：

- 脚本不再 `adb push`、`adb forward`，设备上没有代码或归档暂存。
- 目录名含中文、空格、单引号时仍可正确读取。
- xz、gzip、none 三种模式都能生成并验证归档。
- ADB 不可用、源路径不存在/不是目录、ADB 输出截断时脚本失败。
- 输出先写 `.partial`，校验成功后才移动到最终文件。

## 真机测试

真机测试默认目标是 `/storage/emulated/0/Android/data/com.example.backup/测试.d`，可用
`ANDROBACKUP_DEVICE_DIR` 覆盖。测试不上传被测数据，只通过 `adb exec-out` 读取。

重点用例：

1. 目标目录存在且可由 ADB shell 列出。
2. 文件两次 `exec-out cat` 与 `adb pull` 逐字节一致。
3. 真实 `backup-android.sh` 输出可由本机 `verify` 完整校验。
4. 归档中的 `测试.txt` 与设备源字节一致。
5. 备份前后设备可用空间变化小于 32 MiB，防止未来回退为设备端中转文件。

设备未连接、未授权或路径不可读时应先修复 ADB 权限；测试失败不会自动改写设备内容。

## Windows 状态

`.bat` 与 `.sh` 共享 `paxck.py backup-adb` 入口，但本机没有 Windows cmd 运行环境，暂不
自动执行 `.bat`。在 Windows 上接入 CI 后，应补充：中文路径、`%errorlevel%`、cmd 字节重定向、
`.partial` 失败清理和 PowerShell 文本重定向损坏回归。
