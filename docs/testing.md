# 测试说明

测试分为四层：不依赖设备的 Python 单元测试（包括通用 PAX writer 与 ADB 源适配器）、替身
ADB 驱动的 Linux 主控集成测试、替身 ADB 驱动的 Windows/CMD 主控集成测试，以及连接 Android
设备后的真机集成测试。

## 运行

```sh
# Linux/macOS：标准库即可
python3 -m unittest discover -s tests -t . -v

# 可选 pytest
python3 -m pytest tests -q
```

Windows 在已安装 Python 3 后，从 `cmd.exe` 运行：

```bat
py -m unittest discover -s tests -t . -v
```

真机测试会自动查找 `adb`；也可指定：

```sh
ANDROBACKUP_ADB=/path/to/platform-tools/adb \
python3 -m unittest -v tests.test_device_integration
```

真机测试同时支持两种 ADB 传输。USB 有线设备（单设备）不设置选择变量：

```sh
unset ANDROBACKUP_ADB_SERIAL ANDROBACKUP_ADB_CONNECT
python3 -m unittest -v tests.test_device_integration
```

多台设备/连接同时存在时，建议将 `ANDROBACKUP_ADB_SERIAL` 设为 `adb devices` 输出的目标
serial；测试会用 `adb -s <serial>`，但不会执行 `adb connect`。未设置时，测试会选取第一台已
授权设备并在本次运行中固定该 serial，避免后续命令触发 `more than one device/emulator`。无线
设备则使用 `host:port`；
`ANDROBACKUP_ADB_CONNECT=1` 才会在测试开始时发起连接：

```sh
export ANDROBACKUP_ADB_SERIAL=192.0.2.1:5555
export ANDROBACKUP_ADB_CONNECT=1
python3 -m unittest -v tests.test_device_integration
```

Windows CMD 下的等价用法：

```bat
rem USB 单设备：清空两个变量
set "ANDROBACKUP_ADB_SERIAL="
set "ANDROBACKUP_ADB_CONNECT="
py -m unittest -v tests.test_device_integration

rem 无线：先完成 adb pair，再填写无线连接端点
set "ANDROBACKUP_ADB_SERIAL=192.0.2.1:5555"
set "ANDROBACKUP_ADB_CONNECT=1"
py -m unittest -v tests.test_device_integration
```

Windows 与 Linux 主控的默认现场变量集中在 UTF-8 `src/backup-android.yaml`；无线调试端点
更新时只改其中的 `adb_serial` 行。也可以在包装命令后追加 `--config PATH` 选择任意位置的 YAML，
其优先级高于 `BACKUP_CONFIG_FILE` 和默认文件。离线测试通过将 `BACKUP_CONFIG_FILE` 指向不存在
的文件来隔离现场配置。`.bat` 与 `.sh` 只是调用同一个 `backup.py` 的薄包装。

没有已授权设备时，真机测试会 `skip`，不会使离线 CI 失败。

## 覆盖矩阵

```mermaid
flowchart LR
    U[单元测试\n纯 Python] --> I[离线集成\n替身 adb + 真实 .sh]
    U --> W[Windows 集成\n替身 adb + 真实 .bat/cmd]
    I --> D[真机集成\n真实 adb + Android 15]
    W --> D
    U --> V[校验器\n退出码与损坏归档]
    I --> B[主控脚本\n路径/压缩/失败清理]
    D --> P[用户目标路径\nAndroid/data]
```

| 文件 | 类型 | 重点 |
|---|---|---|
| `tests/test_paxck_unit.py` | 单元 | 通用 PAX writer、魔术字节、流读取器、PAX 哈希、符号链接、硬链接、变化文件、压缩、校验退出码，以及 `adb_source` 的 Android `%Y/%y` 元数据解析 |
| `tests/test_pipeline_local.py` | 离线集成 | 独立本机 `create | compress | verify`、系统 tar 互操作、还原保真、非 UTF-8 文件名 |
| `tests/test_backup_sh_integration.py` | 离线集成 | 真实 `.sh` + 替身 ADB；`backup.py -> adb_source.py -> paxck.py` 组合、USB serial（不 connect）与 TCP serial（connect）、中文/空格/单引号路径、二进制安全、截断、错误退出、Android/data 路径 |
| `tests/test_backup_bat_integration.py` | Windows 离线集成 | 真实 `cmd.exe` + `.bat` + `.cmd` 替身 ADB；`backup.py -> adb_source.py -> paxck.py` 组合、USB serial（不 connect）与 TCP serial（connect）、UTF-8 路径、二进制重定向、gzip/裸 tar、失败清理、命令行 YAML 路径 |
| `tests/test_device_integration.py` | 真机集成 | ADB 授权、目标目录读取、双遍字节一致性、平台对应主控备份、设备空间不生成中间文件 |

## 单元测试要点

`TestConfig` 覆盖 UTF-8 YAML 的引号、布尔值、行尾注释和格式错误；同一解析器由 Windows
和 POSIX 主控共享。

`TestAdbSourceMetadata` 用 toybox 风格的 `%Y`/`%y` 输出验证 `adb_source.py` 保留 Android
亚秒 `mtime`，并直接验证其目录、普通文件和符号链接均经通用 PAX writer 写入，避免回归为
只读取整数秒或把 ADB 打包逻辑重新耦合回 `paxck.py`。

发布验证还应在 Python 3.10、3.13 和 3.14 至少各运行一次离线测试：3.10/3.13 的 zstd
用例需要 PATH 中的 `zstd`，3.14 可验证标准库 `compression.zstd` 路径。xz、gzip 和裸 tar
不需要任何第三方 Python 包。

`paxck.py create` 的样例树包含普通文件、空文件、跨 1 MiB 边界的大文件、中文和空格名、
指向文件/目录的符号链接、断链以及 FIFO。测试确认：

- PAX 扩展头中的 SHA-256 与归档内容一致。
- 指向目录的符号链接保持 `SYMTYPE`，不会被 `os.walk` 错写成空目录。
- 文件在两遍读取间被删除、缩短或权限改变时不会产出“成功”的坏归档。
- ADB/管道的合法短读不会被误判为 EOF。
- 归档缺条目、截断、内容翻转或没有任何 SHA-256 记录时，`verify` 返回 1。

## 离线主控集成

替身 ADB 在本机临时目录上实现 `get-state`、可选 `connect` 与 `exec-out sh -c ...`，真实执行
find/stat/readlink/cat。主控实际组合 `adb_source.py` 的裸 PAX 输出与 `paxck.py compress`，这样
不依赖手机，也能验证：

- 脚本不再 `adb push`、`adb forward`，设备上没有代码或归档暂存。
- 目录名含中文、空格、单引号时仍可正确读取。
- xz、gzip、none 三种模式都能生成并验证归档。
- ADB 不可用、源路径不存在/不是目录、ADB 输出截断时脚本失败。
- 输出先写 `.partial`，校验成功后才移动到最终文件。
- USB 模式显式传入 serial 时不调用 `adb connect`；无线模式传入 `host:port` 时先调用
  `adb connect`，并将同一 serial 传递给后续所有 ADB 子进程。

Windows 用例会由 Python 真正启动 `cmd.exe /d /c backup-android.bat`，而不是模拟批处理
语法。替身 ADB 本身是一个 `.cmd` 文件，继续转交给 Python，以覆盖 Windows 的命令行参数
传递、`cmd` 的二进制重定向、`%ERRORLEVEL%` 分支，以及 `.partial.<random>` 清理。该层还
验证 `ADB_SERIAL` 被映射为子进程可见的 `ANDROID_SERIAL`，并在 `ADB_CONNECT=1` 时先执行
`adb connect`。样例文件含有从 `0x00` 到 `0xff` 的全部字节，专门覆盖 LF 不得变为 CRLF 的
回归风险。

Windows 的系统 `tar.exe` 会将归档中 POSIX 绝对符号链接目标的 `/` 还原成当前驱动器根下的
`\`。还原保真测试只对这类 Windows 特有表示做比较规范化；归档级单元测试仍直接断言 PAX/tar
头中的原始 `linkname`，因此不会掩盖打包器改变链接目标的问题。

## 真机测试

真机测试默认目标是 `/storage/emulated/0/Android/data/com.example.backup/测试.d`，可用
`ANDROBACKUP_DEVICE_DIR` 覆盖。测试不上传被测数据，只通过 `adb exec-out` 读取。

重点用例：

1. 目标目录存在且可由 ADB shell 列出。
2. 文件两次 `exec-out cat` 与 `adb pull` 逐字节一致。
3. 真实 `backup-android.sh`（Linux）或 `backup-android.bat`（Windows）输出可由本机 `verify`
   完整校验；通过环境变量可分别运行 USB 与无线传输。
4. 归档中的 `测试.txt` 与设备源字节一致。
5. 备份前后设备可用空间变化小于 32 MiB，防止未来回退为设备端中转文件。
6. 对含 LF 的真实设备文件，Windows `adb exec-out cat` 与 `adb pull` 的字节完全一致。

设备未连接、未授权或路径不可读时应先修复 ADB 权限；测试失败不会自动改写设备内容。要
覆盖两种物理链路，应分别运行一次 USB（不设置 `ANDROBACKUP_ADB_SERIAL`，或填写 USB
serial）和无线（`ANDROBACKUP_ADB_SERIAL=host:port`）测试；无线连接动作只在同时设定
`ANDROBACKUP_ADB_CONNECT=1` 时执行。没有对应设备时，测试会自动 `skip`。
