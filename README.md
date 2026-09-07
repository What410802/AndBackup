# AndBackup

将已开启 USB 调试的 Android 14+ 设备中的一个目录直接写入主机端
`tar.xz`、`tar.gz`、`tar.zst` 或裸 `tar`。整个过程中设备上不会生成 tar、压缩包或
临时文件，归档落盘后还会逐文件校验 SHA-256。

项目针对一个常见但受限的场景：Android 11+ 禁止普通应用读取其他应用的
`Android/data`；因此不使用 Termux/SSH，而是让主机通过授权的 `adb exec-out` 以
Android shell 用户读取源文件。实机已验证的路径为
`/storage/emulated/0/Android/data/com.example.backup/测试.d`。

```text
Android 文件 -> adb exec-out -> 主机 paxck.py -> tar + 压缩 -> backup.tar.xz
                         USB                         |
                                                     SHA-256 校验
```

## 快速开始

准备条件：

- Android 设备开启 USB 调试，并在设备上授权当前主机。
- 主机安装 Python 3.10+ 与 Android platform-tools 的 `adb`。
- Linux 运行 `src/backup-android.sh`；Windows 从 `cmd.exe` 运行
  `src\backup-android.bat`。

Linux 示例：

```sh
cd src
ADB=/home/bis/BiS.d/Code.d/third_party/android-sdk/platform-tools/adb \
SOURCE_DIR='/storage/emulated/0/Android/data/com.example.backup/测试.d' \
OUT=android-backup.tar.xz \
./backup-android.sh
```

Windows 示例（当前无线调试设备）：

```bat
cd src
set "ADB_SERIAL=192.0.2.1:5555"
set "ADB_CONNECT=1"
set "SOURCE_DIR=/storage/emulated/0/Android/data/com.example.backup/测试.d"
set "OUT=android-backup.tar.xz"
backup-android.bat
```

`ADB_CONNECT=1` 会先执行一次 `adb connect %ADB_SERIAL%`；设备已连接时也可省略它。
首次使用无线调试时，先在 Android 的“无线调试”页面完成 `adb pair <host>:<pair-port>`。
必须在 `cmd.exe` 中运行 `.bat`，不要把 `backup-adb` 命令改写为 PowerShell 的 `>`
重定向，压缩流必须按字节写入。

也可以直接编辑 UTF-8 的 `src\backup-android.yaml`。当前无线 ADB 地址位于该文件的
`adb_serial` 行（现在是 `192.0.2.1:5555`）；Android 每次显示新端口时只需更新这一
行。若不希望修改仓库文件，可在 cmd 或 sh 中设置 `BACKUP_CONFIG_FILE` 指向自己的 YAML。

Linux 与 Windows 主控共用以下环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ADB` | `adb` | `adb` 可执行文件或绝对路径 |
| `SOURCE_DIR` | `/sdcard/DCIM` | 设备上的绝对目录 |
| `OUT` | 随压缩类型决定 | 主机端归档路径 |
| `COMPRESS` | `xz` | `xz`、`gzip`、`zstd` 或 `none` |
| `PYTHON` | 自动查找 | Windows 上 Python 解释器的完整路径（可选） |
| `ADB_SERIAL` | 未指定 | 要使用的设备 serial；无线设备填 `host:port` |
| `ADB_CONNECT` | 未指定 | 设为 `1`/`true` 时先连接 `ADB_SERIAL`（Windows 与 Linux 均适用） |
| `BACKUP_CONFIG_FILE` | `src\backup-android.yaml` | UTF-8 现场配置文件路径；设为空/不存在可禁用 |

`xz` 与 `gzip` 只用 Python 标准库。`zstd` 需要 Python 3.14+ 的
`compression.zstd`，或主机 `PATH` 中的 `zstd`；缺少时会以退出码 2 失败。

YAML 配置只使用顶层键值（字符串可用单引号或双引号），例如：

```yaml
adb: adb
adb_serial: 192.0.2.1:5555
adb_connect: true
source_dir: "/storage/emulated/0/Android/data/com.example.backup/测试.d"
out: android-backup.tar.xz
compress: xz
```

同名环境变量优先于 YAML；将 `BACKUP_CONFIG_FILE` 设为空可完全禁用默认配置。配置解析
由 `paxck.py` 内置完成，不需要安装 PyYAML。

## 工作方式

`.bat` 与 `.sh` 都只是启动同一个跨平台 Python 主控；它读取 YAML 后调用一个主机端进程：

```sh
python3 paxck.py backup-adb \
  --adb /path/to/adb \
  --compress xz \
  /storage/emulated/0/Android/data/com.example.backup/测试.d \
  > backup.tar.xz
```

设备端不使用 `tar`、`xz`、`gzip` 或 Python。为了在不落盘的情况下枚举并读取文件，
ADB shell 只调用系统自带的 `find -print0`、`stat`、`readlink` 和 `cat`；这部分无法由
主机替代。主机端用 Python 标准库流式写 tar 和 xz/gzip，内存不会随归档总大小增长。

读取命令始终使用 `adb exec-out`，而不是把 `adb shell` 的输出经 Windows 控制台或文本管道
转发。`paxck.py` 从二进制子进程管道读取字节，CMD 只重定向 Python 的二进制 stdout，因此
文件内的 LF (`0x0A`) 不会被转换成 CRLF (`0x0D 0x0A`)。

每个普通文件经 USB 读取两遍：第一遍取得 SHA-256 与长度，第二遍直接写入 tar。若文件
在两遍之间变更、短读或 ADB 返回错误，整个命令退出非零，主控不会把部分备份报告为成功。

`paxck.py verify` 自动识别裸 tar、xz 与 gzip；它检查：

1. 压缩流与 tar 结构是否完整。
2. 归档是否确实有条目。
3. 每个普通文件的 `PAXCK.checksum.sha256` 是否与内容匹配。

没有这项 PAX 记录的普通 tar 会被拒绝，而不是显示“0 个失败”。

## 项目结构

```text
src/
  paxck.py                 主机端归档、压缩和校验器
  backup.py                 跨平台主控（配置、ADB、原子输出与校验）
  backup-android.sh        POSIX 启动包装（转发至 backup.py）
  backup-android.bat       Windows CMD 启动包装（转发至 backup.py）
  backup-android.yaml       跨平台现场变量（无线 ADB serial 等）
docs/
  flow.md                  数据流、错误语义与边界
  testing.md               测试层次、覆盖范围和运行方式
tests/                     单元、离线集成和真机集成测试
```

## 限制与安全边界

- 需要 USB 调试授权。不同 ROM 的 ADB shell 存储权限可不同；先运行测试或用
  `adb shell ls -la <SOURCE_DIR>` 确认可读。
- 本工具不读取受 Android 应用私有沙箱保护的 `/data/user/*`；它针对外部存储上的
  `Android/data/*` 与其他 ADB shell 可读目录。
- 源文件在备份中持续变化时，工具会失败而非产生不完整的“成功”备份；暂停相关应用后重试。
- Windows `.bat` 及其 `cmd.exe` 二进制重定向已有离线自动化覆盖；仍建议在首次使用的
  设备上运行真机集成测试确认 ROM 的 ADB shell 存储权限。

## 测试

```sh
python3 -m unittest discover -s tests -t .
python3 -m pytest tests -q
```

测试只依赖标准库；设备不存在或未授权时，真机测试会自动 skip。可显式指定 ADB：

```sh
ANDROBACKUP_ADB=/path/to/adb \
python3 -m unittest -v tests.test_device_integration
```

Windows 无线调试设备可这样运行真机测试：

```bat
set "ANDROBACKUP_ADB_SERIAL=192.0.2.1:5555"
set "ANDROBACKUP_ADB_CONNECT=1"
py -m unittest -v tests.test_device_integration
```

完整测试说明见 [docs/testing.md](docs/testing.md)，设计说明见
[docs/flow.md](docs/flow.md)。
