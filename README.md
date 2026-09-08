# AndBackup

[English README](README.en.md) | [中文技术说明](docs/flow.md) | [中文测试说明](docs/testing.md)

项目由两个可独立使用的组件组成：`paxck.py` 将**本机目录**写成带 PAX 内嵌 SHA-256 的
裸 `tar`，并可压缩、校验或提取；`adb_source.py` 则把已开启 ADB 调试（USB 有线或
TCP 无线）的 Android 目录作为同一打包器的数据源。两者组合时，设备上不会生成 tar、压缩包
或临时文件，归档落盘后还会逐文件校验 SHA-256。

项目针对一个常见但受限的场景：Android 11+ 禁止普通应用读取其他应用的
`Android/data`；因此不使用 Termux/SSH，而是让主机通过授权的 `adb exec-out` 以
Android shell 用户读取源文件。实机已验证 ADB shell 可读取
`/storage/emulated/0` 下的目录。

```text
Android 文件 -> adb exec-out -> adb_source.py -> paxck.py -> tar + 压缩
                    USB 或 TCP                              |
                                                        SHA-256 校验
```

## 快速开始

准备条件：

- Android 设备开启 ADB 调试，并在设备上授权当前主机；可使用 USB 有线调试或无线调试。
- 主机安装 Python 3.12+ 与 Android platform-tools 的 `adb`。
- Linux 运行 `src/backup-android.sh`；Windows 从 `cmd.exe` （而非 PowerShell ）运行 `src\backup-android.bat`。

先将跟踪的模板复制为本地配置，再编辑副本。`src/backup-android.yaml` 被 `.gitignore` 忽略，
不会随提交包含设备 serial、无线端点或本机输出路径：

```sh
cp src/backup-android.example.yaml src/backup-android.yaml
```

```bat
copy src\backup-android.example.yaml src\backup-android.yaml
```

USB 与无线 ADB 使用同一个启动脚本，区别只在 `adb_serial`/`adb_connect`：

USB 有线 ADB（单设备时可留空 `adb_serial`）：

```yaml
adb: adb
adb_serial: ""
adb_connect: false
source_dir: "/storage/emulated/0/DCIM"
out: android-backup.tar.xz
compress: xz
log_level: info
progress_interval: 5
```

示例 1：Linux

```sh
src/backup-android.sh
```

如之前未连接过ADB（USB或局域网），先确认设备已授权：

```sh
adb devices
```

多台设备同时在线时，把 `adb_serial` 填为 `adb devices` 第一列的 USB serial；单台设备可以
留空，由 ADB 自动选择。

无线 ADB（TCP serial）：

1. 在 Android“无线调试”页面完成 `adb pair <主机>:<配对端口>`。
2. 将 YAML 中的 `adb_serial` 填为设备显示的连接地址 `设备IP:连接端口`，并设
   `adb_connect: true`。
3. 运行同一个 `backup-android.sh` 或 `backup-android.bat`；脚本会先执行 `adb connect`。

无线连接配置示例：

```yaml
adb_serial: 设备IP:连接端口
adb_connect: true
```

示例 2：Windows CMD

```bat
cd src
backup-android.bat
```

无线设备已经连接时可将 `adb_connect` 改为 `false`；Android 每次显示新的无线连接端口时，
只需更新 YAML 的 `adb_serial` 行。USB 配置不要将 `adb_connect` 设为 `true`，因为
`adb connect` 仅适用于 TCP serial。

YAML 可以放在任意路径。`.bat`/`.sh` 会把参数原样交给 `backup.py`：

```sh
./src/backup-android.sh --config /path/to/site-backup.yaml
```

```bat
src\backup-android.bat --config D:\backup-config\site-backup.yaml
```

配置文件选择优先级为 `--config PATH`、`BACKUP_CONFIG_FILE`、脚本目录中默认的
`src/backup-android.yaml`（存在时）。显式传入但不存在的 `--config` 会报错；将
`BACKUP_CONFIG_FILE` 设为空或设为不存在路径，则不加载默认 YAML，适合自动化测试或完全使用
环境变量的场景。同名业务环境变量（如 `OUT`、`ADB_SERIAL`）始终优先于 YAML 内的值。

Linux 与 Windows 主控共用以下环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ADB` | `adb` | `adb` 可执行文件或绝对路径 |
| `SOURCE_DIR` | `/sdcard/DCIM` | 设备上的绝对目录 |
| `OUT` | 随压缩类型决定 | 主机端归档路径 |
| `COMPRESS` | `xz` | `xz`、`gzip`、`zstd` 或 `none` |
| `PYTHON` | 自动查找 | Windows 上 Python 解释器的完整路径（可选） |
| `ADB_SERIAL` | 未指定 | 要使用的设备 serial；USB 填 `adb devices` 的 serial，无线填 `host:port` |
| `ADB_CONNECT` | 未指定 | 无线 TCP serial 设为 `1`/`true` 时先执行 `adb connect`；USB 应保持关闭 |
| `BACKUP_CONFIG_FILE` | 未设置时尝试 `src\backup-android.yaml` | UTF-8 配置文件路径；空值/不存在路径不加载 YAML |
| `LOG_LEVEL` | `info` | `quiet`、`error`、`warn`、`info`、`debug`、`trace` |
| `PROGRESS_INTERVAL` | `5` | 进度输出间隔（秒），最小 `0.1` |

`xz` 与 `gzip` 只用 Python 标准库。`zstd` 需要 Python 3.14+ 的
`compression.zstd`，或主机 `PATH` 中的 `zstd`；缺少时会以退出码 2 失败。

YAML 配置只使用顶层键值（字符串可用单引号或双引号）；上面的 USB/无线片段可合并为完整配置。
配置解析由 `backup.py` 内置完成，不需要安装 PyYAML。

## 发布与 Python 依赖

项目没有 `requirements.txt`，也不需要安装第三方 Python 包。`src/backup.py`、
`src/paxck.py`、`src/adb_source.py`、配置解析器和测试使用的都是 Python 标准库；Android
模式的主机端唯一必需外部工具是 Android SDK Platform-Tools 中的 `adb`。

Python 版本不必固定到某一个补丁版本，但发布时应声明并测试版本范围：

- Python **3.12 或更高版本**：支持 `xz`、`gzip`、裸 `tar`、ADB 读取、PAX SHA-256 和
  YAML 配置。
- Python **3.14 或更高版本**：可直接使用标准库 `compression.zstd` 生成和校验 zstd。
- Python **3.12--3.13**：选择 zstd 时需要另外安装主机 `zstd` 命令并放入 `PATH`；否则使用
  `xz`（默认）或 `gzip`。

因此，普通用户无需创建虚拟环境或锁定依赖；推荐发布包/CI 固定一个最低版本（当前为
3.12）并覆盖 3.12、3.13、3.14 的测试矩阵。若需要可复现的 zstd 字节流，应同时固定
Python 版本和 zstd 外部命令版本，因为压缩器实现和参数会影响输出字节，但不影响归档内容。

截至 `v0.1.0` 发布准备，完整离线套件已在 Windows CPython 3.13.15 上运行；WSL Ubuntu
CPython 3.14.4 已运行跨平台选择用例。Python 3.12 是声明的最低版本，并由提交后的 CI
矩阵覆盖，但本机未为此额外安装解释器。Ubuntu 24.04 的系统 Python 为 3.12；Debian 12
的系统 Python 为 3.11，需另行安装或提供 3.12+ 解释器。

`adb` 不是 Python 包，需按 Android 官方 Platform-Tools 的版本和目标系统单独分发或要求用户
安装。设备端不需要 Python、tar、xz、gzip 或 zstd。

## 打包器与 Android 数据源

`paxck.py` 不依赖 ADB，可单独用于任何本机目录。以下命令创建和校验本机归档：

```sh
python3 src/paxck.py create /path/to/local-directory \
  | python3 src/paxck.py compress xz > local.tar.xz
python3 src/paxck.py verify -i local.tar.xz
python3 src/paxck.py extract -i local.tar.xz -C local-restored
```

Windows 请从 `cmd.exe` 运行等价命令，避免 PowerShell 的文本管道影响二进制流：

```bat
py src\paxck.py create "C:\path\to\local-directory" ^
  | py src\paxck.py compress xz > local.tar.xz
py src\paxck.py verify -i local.tar.xz
py src\paxck.py extract -i local.tar.xz -C local-restored
```

`adb_source.py` 是 Android 专用的数据源适配器：它通过 `adb exec-out` 写出裸 PAX tar，
再交给通用压缩器。手动组合可用于脚本集成：

```sh
python3 src/adb_source.py --adb /path/to/adb \
  /storage/emulated/0/DCIM \
  | python3 src/paxck.py compress xz > android.tar.xz
python3 src/paxck.py verify -i android.tar.xz
```

日常 Android 备份应使用 `.bat`/`.sh` 或 `backup.py`，而不是直接运行这条管道。主控会在 Python
中检查 `adb_source.py` 和 `paxck.py compress` 两端退出码，先写 `OUT.partial.*`、运行校验，
成功后才原子替换 `OUT`。普通 shell 管道通常只能可靠取得最后一个命令的退出码，也没有上述
临时文件保护。

设备端不使用 `tar`、`xz`、`gzip` 或 Python。为了在不落盘的情况下枚举并读取文件，
ADB shell 只调用系统自带的 `find -print0`、`stat`、`readlink` 和 `cat`；这部分无法由
主机替代。主机端的 `adb_source.py` 传递条目和数据，`paxck.py` 用 Python 标准库流式写 tar
和 xz/gzip，内存不会随归档总大小增长。

读取命令始终使用 `adb exec-out`，而不是把 `adb shell` 的输出经 Windows 控制台或文本管道
转发。`adb_source.py` 从二进制子进程管道读取字节，CMD 只重定向 Python 的二进制 stdout，因此
文件内的 LF (`0x0A`) 不会被转换成 CRLF (`0x0D 0x0A`)。

每个普通文件经 ADB 通道（USB 或 TCP）读取两遍：第一遍取得 SHA-256 与长度，第二遍直接写入 tar。若文件
在两遍之间变更、短读或 ADB 返回错误，整个命令退出非零，主控不会把部分备份报告为成功。若目录中只有
部分条目受 scoped storage/权限限制，适配器会警告并跳过它们，生成可校验的部分归档后返回 `3`；主控仍
不会替换最终备份。详见[错误流与权限语义](docs/flow.md#adb-错误流与权限错误)。

`paxck.py verify` 自动识别裸 tar、xz 与 gzip；它检查：

1. 压缩流与 tar 结构是否完整。
2. 归档是否确实有条目。
3. 每个普通文件的 `PAXCK.checksum.sha256` 是否与内容匹配。

没有这项 PAX 记录的普通 tar 会被拒绝，而不是显示“0 个失败”。

## 公开接口

以下是 `v0.1.0` 对外支持的命令行接口。所有归档字节都走二进制 stdin/stdout；在 Windows
应从 `cmd.exe` 使用管道和重定向，避免 PowerShell 的文本管道改变数据。

| 入口 | 用法 | 行为 |
|---|---|---|
| 本机打包 | `paxck.py create DIRECTORY` | 将 `DIRECTORY` 作为根目录写成裸 PAX tar 到 stdout；普通文件带 `PAXCK.checksum.sha256`。 |
| 压缩 | `paxck.py compress {xz,gzip,zstd,none}` | 从 stdin 读原始字节并写到 stdout。`xz`、`gzip`、`none` 只需标准库。 |
| 校验 | `paxck.py verify [ARCHIVE]` 或 `-i ARCHIVE`，可加 `-q` | 自动识别裸 tar、xz、gzip、zstd，验证每个普通文件的 PAX SHA-256。 |
| 默认提取 | `paxck.py extract [ARCHIVE] -C DEST` 或 `-i ARCHIVE` | `DEST` 必须尚不存在；在同级临时目录逐文件校验 SHA-256、拒绝不安全路径/未校验普通文件，成功后原子改名发布。 |
| 直接提取 | `paxck.py extract --direct-tarfile [ARCHIVE] -C DEST`（`--direct` 为别名） | 直接调用 Python `tarfile`，允许已有 `DEST`，不验证 PAX SHA-256，也不具有原子性。仅用于可信归档或互操作；失败可留下部分文件。 |
| Android 源适配器 | `adb_source.py [--adb ADB] [--log-level LEVEL] [--progress-interval SECONDS] DIRECTORY` | 经 `adb exec-out` 将 Android 绝对目录写为裸 PAX tar 到 stdout；进度/流量统计写 stderr。 |
| Android 主控 | `backup.py [--config PATH] [--log-level LEVEL] [--progress-interval SECONDS]` | 读取配置/环境，组合 ADB 适配器与压缩器，校验 `.partial` 后原子替换最终归档。 |
| 平台包装 | `backup-android.sh [ARGS...]`；`backup-android.bat [ARGS...]` | 仅转发所有参数给同目录 `backup.py`；前者用于 POSIX shell，后者用于 Windows CMD。 |

`paxck.py --version`、`adb_source.py --version` 和 `backup.py --version` 输出同一个发布版本。
`backup.py` 的公开配置键为 `adb`、`adb_serial`、`adb_connect`、`source_dir`、`out`、`compress`、
`log_level`、`progress_interval`；命令行也可用 `--log-level` 和 `--progress-interval` 覆盖。
同名环境变量 `ADB`、`ADB_SERIAL`、`ADB_CONNECT`、`SOURCE_DIR`、`OUT`、`COMPRESS` 优先于 YAML。
`PYTHON` 和 `BACKUP_CONFIG_FILE` 是包装/配置选择环境变量，含义见上表。
上表以外的 Python 模块函数、类和常量都是实现细节，不构成稳定公开 API。

提取命令成功信息只在 stdout 输出，失败诊断只在 stderr 输出。默认模式成功时显示“已验证并提取”，
直接模式明确显示“未校验 PAX SHA-256，非原子”，不能把后者当作备份恢复验证。

Android 归档会同时读取 `stat` 的 `%Y` 与 `%y`，并保留 `%y` 暴露的小数部分；当前实测设备的
`/storage/emulated/0/Android/data/...` 文件和目录显示 9 位小数（纳秒格式）。这表示接口
至少暴露了纳秒格式的值，不保证所有 ROM/FUSE/文件系统都实际以纳秒写入；本机目录模式可
通过 PAX 保存小数秒，最终精度仍取决于源文件系统和 Python 时间戳表示。访问时间、创建时间、状态改变时间以及 Android UID/GID 当前不写入归档；其中
一部分是项目的格式取舍，另一部分可能受 Android `shell` UID、scoped storage、Unix 权限和
`/storage` 文件系统抽象限制。详细字段和边界见 [docs/flow.md](docs/flow.md) 的“时间精度与
Android 元数据来源”。

## 与 Android 端打包方案的区别

另一种可行设计是把 tar（以及可选压缩程序）上传到 Android `/data/local/tmp/`，在设备端
读取目录并把归档流回传主机。两种方案在“解压后得到的普通文件内容”上可以做到一致，但不应
期待 tar 文件字节或所有元信息一致：

| 方面 | 当前主机打包 | Android 端 tar 打包 |
|---|---|---|
| tar/压缩实现 | 主机 Python `tarfile`、`lzma`/`gzip`/zstd | 上传或设备自带的 tar/压缩程序 |
| 设备端工作 | `find`、`stat`、`readlink`、`cat` | tar 自行遍历、读取并写归档 |
| 中间存储 | 主机 `.partial`；设备不落盘 | 需要在 `/data/local/tmp` 保存可执行文件，可能还产生设备端临时输出 |
| 文件内容校验 | 每个普通文件两遍读取，PAX 内嵌 SHA-256 | 通常单遍读取，除非另行实现校验 |
| 变化文件语义 | 两遍长度/读取不一致则失败 | 取决于 tar 实现，通常没有相同保证 |
| 硬链接 | ADB 模式不恢复 inode 关系 | tar 可能恢复，取决于实现和参数 |
| 特殊文件 | FIFO、socket、设备节点跳过 | tar 可能记录或尝试读取 |
| UID/GID | ADB 模式不恢复 Android UID/GID，使用默认值 | 可能保留 Android UID/GID |
| xattr/ACL/SELinux | 不保存 | 取决于 tar、权限和参数 |
| 条目顺序与头部 | 由本项目固定排序和 PAX 规则 | 由设备端 tar 版本和参数决定 |

若两边明确采用相同的条目集合、路径、时间/权限策略和 tar 参数，逻辑文件树可以等价；但
不同实现的 tar 头、PAX 扩展、硬链接表示和压缩参数通常会使最终归档字节不同。当前方案的
额外保证是设备端不需要执行上传的二进制，且主机在替换最终文件前会验证每个普通文件的
SHA-256。

## 项目结构

```text
src/
  paxck.py                 通用本机 PAX 归档、压缩、校验和提取器
  adb_source.py            Android ADB 数据源适配器（写裸 PAX tar）
  backup.py                 跨平台 Android 主控（配置、组合、原子输出与校验）
  backup-android.sh        POSIX 启动包装（转发至 backup.py）
  backup-android.bat       Windows CMD 启动包装（转发至 backup.py）
  backup-android.example.yaml  无现场信息的配置模板
  backup-android.yaml       本地现场配置（忽略，不随提交）
docs/
  flow.md                  数据流、错误语义与边界
  testing.md               测试层次、覆盖范围和运行方式
  release-0.1.0.md         首版发布说明草稿与检查表
tests/                     单元、离线集成和真机集成测试
```

## 限制与安全边界

- 需要 ADB 调试授权（USB 或无线）。不同 ROM 的 ADB shell 存储权限可不同；先运行测试或用
  `adb shell ls -la <SOURCE_DIR>` 确认可读。
- 本工具不读取受 Android 应用私有沙箱保护的 `/data/user/*`；它针对外部存储上的
  `Android/data/*` 与其他 ADB shell 可读目录。
- 源文件在备份中持续变化时，工具会失败而非产生不完整的“成功”备份；暂停相关应用后重试。
- `paxck.py extract` 默认只接受由本项目写出的、普通文件带 SHA-256 记录的安全归档，并且目标
  目录不得已存在；使用 `--direct-tarfile` 即明确放弃这两项保护，不能对不可信输入使用。
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
ANDROBACKUP_DEVICE_DIR=/storage/emulated/0/DCIM \
python3 -m unittest -v tests.test_device_integration
```

Windows 无线调试设备可这样运行真机测试（USB 设备则省略这两个变量，或将 serial 设置为
`adb devices` 中的 USB serial；不要设置 `ANDROBACKUP_ADB_CONNECT=1`）：

```bat
set "ANDROBACKUP_ADB_SERIAL=192.0.2.1:5555"
set "ANDROBACKUP_ADB_CONNECT=1"
set "ANDROBACKUP_DEVICE_DIR=/storage/emulated/0/DCIM"
py -m unittest -v tests.test_device_integration
```

完整测试说明见 [docs/testing.md](docs/testing.md)，设计说明见
[docs/flow.md](docs/flow.md)。

## 许可证

本项目采用 [MIT License](LICENSE)；版本记录见 [CHANGELOG.md](CHANGELOG.md)，首版发布说明和
发布前检查表见 [docs/release-0.1.0.md](docs/release-0.1.0.md)。
