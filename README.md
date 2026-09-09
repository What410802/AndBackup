# AndBackup

[English README](README.en.md) | [中文技术说明](docs/flow.md) | [中文测试说明](docs/testing.md) | [配置与命令行参考](docs/configuration.md)

项目由两个可独立使用的组件组成：`paxck.py` 将**本机目录**写成带 PAX 内嵌 SHA-256 的
裸 `tar`，并可压缩、校验或提取；`adb_source.py` 则把已开启 ADB 调试（USB 有线或
TCP 无线）的 Android 目录作为同一打包器的数据源。归档落盘后还会逐文件校验 SHA-256。
Android 数据源可显式选择两种模式，且不会自动互相切换：

- `host-adb`（默认）：主机通过 `adb exec-out` 逐条读取，设备端只运行
  `find`/`stat`/`readlink`/`cat`，不生成 tar、压缩包或临时文件。
- `device-python`：把本机提供的 Android ARM64 Python 与 `paxck.py` 上传到设备固定目录
  `/data/local/tmp/andbackup-pyenv`，在设备端流式生成 tar 再回传主机压缩；设备端不生成
  tar 文件或压缩包，解释器会按缓存规则复用/清理（见
  [docs/configuration.md](docs/configuration.md)）。

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
show_rate: false
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

完整的 YAML 键、环境变量、命令行（含 `--clean-env`/`--clean-host-cache`）、缓存与清理、
压缩/zstd 说明见 **[docs/configuration.md](docs/configuration.md)**；数据流与元数据边界见
[docs/flow.md](docs/flow.md)，测试说明见 [docs/testing.md](docs/testing.md)。

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
安装。`host-adb` 模式设备端不需要 Python、tar、xz、gzip 或 zstd；`device-python` 模式的
Android 解释器默认由脚本按需从 python-build-standalone 下载到缓存
（`download_device_python: true`），也可由 `device_python` 指定已有的单文件/prefix。

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

两种模式都由同一个 `backup.py` 主控驱动，都会在主机端压缩、校验 `.partial` 并原子替换
最终归档，归档格式与逐文件 PAX SHA-256 语义一致；所选模式失败时不会自动切换到另一种。

`host-adb`（默认）设备端不使用 `tar`、`xz`、`gzip` 或 Python。为了在不落盘的情况下
枚举并读取文件，ADB shell 只调用系统自带的 `find -print0`、`stat`、`readlink` 和
`cat`；这部分无法由主机替代。主机端的 `adb_source.py` 传递条目和数据，`paxck.py` 用
Python 标准库流式写 tar 和 xz/gzip，内存不会随归档总大小增长。

`device-python` 把 `DEVICE_PYTHON` 指向的本机 Android ARM64 Python（单文件解释器，
或含 `bin/`+`lib/` 的 prefix 目录，后者会被打包为一个 tar 上传并在设备端解压）和
`src/paxck.py` 上传到设备固定目录 `/data/local/tmp/andbackup-pyenv`，在设备端运行
`paxck.py create SOURCE_DIR` 直接生成裸 PAX tar 到 stdout，再由主机的 `paxck.py
compress` 压缩。它适合大量小文件（`host-adb` 每个文件需要多次 ADB 往返）或希望把目录
遍历/打包放在设备端完成的场景；代价是需要你自行提供与设备 ABI/linker 兼容的 Python
（常见做法是 python-build-standalone 等静态 musl aarch64 构建），并遵守主机下载与设备端
缓存的复用/清理语义（见 [docs/configuration.md](docs/configuration.md) 的缓存说明）。
两种模式都不在设备上生成 tar 文件或压缩包，最终压缩与校验都在主机完成。

解释器不随仓库分发，而是按需获取：设置 `download_device_python: true` 后，若
`device_python` 为空（或指向尚不存在的路径），`backup.py` 会从
[astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone)
的固定 Release（`device_python_url` 可覆盖，也可填本地 `.tar.zst` 以离线复用）下载并解压
到 `device_python` 或默认缓存（Windows `%LOCALAPPDATA%\andbackup`，POSIX
`~/.cache/andbackup`），之后复用缓存、不再联网。解压不依赖第三方 Python 包：
Python 3.14+ 用标准库 `compression.zstd`，否则用外部 `zstd`，否则用系统 `tar`
（Windows `bsdtar` 原生支持 zstd）。

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

## 公开接口与差异对比

`paxck.py`/`adb_source.py`/`backup.py` 的完整命令行表、退出码与校验/提取语义见
[docs/configuration.md](docs/configuration.md)；归档元信息字段、时间精度与 Android 权限边界、
以及与“上传独立 tar 二进制到设备端”的差异对比见 [docs/flow.md](docs/flow.md)。

## 项目结构

```text
src/
  paxck.py                 通用本机 PAX 归档、压缩、校验和提取器
  adb_source.py            Android ADB 数据源适配器（写裸 PAX tar）
  android_python.py        device-python 解释器引导：按需从 python-build-standalone 下载/解压
  backup.py                 跨平台 Android 主控（配置、组合、原子输出与校验）
  backup-android.sh        POSIX 启动包装（转发至 backup.py）
  backup-android.bat       Windows CMD 启动包装（转发至 backup.py）
  backup-android.example.yaml  无现场信息的配置模板
  backup-android.yaml       本地现场配置（忽略，不随提交）
docs/
  flow.md                  数据流、错误语义与边界
  testing.md               测试层次、覆盖范围和运行方式
  configuration.md         配置、命令行与缓存清理参考（中文）
  configuration.en.md      配置、命令行与缓存清理参考（English）
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
