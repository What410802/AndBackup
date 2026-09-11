# 配置与命令行参考

[English version](configuration.en.md) | [中文 README](../README.md) | [数据流与边界](flow.md)

本项目把用户面用法放在 [README](../README.md)，把完整配置、命令行与缓存清理细节集中在本页。

## 配置来源与优先级

1. 命令行 `--config PATH`（优先级最高）
2. 环境变量 `BACKUP_CONFIG_FILE`
3. 脚本同目录默认 `src/backup-android.yaml`（存在时）
4. 业务环境变量（`ADB`、`HOST`、`DEVICE_ID`、`SOURCE_DIR`、`OUT`、`COMPRESS`、`SOURCE_MODE`、`DEVICE_PYTHON`、
   `DOWNLOAD_DEVICE_PYTHON`、`DEVICE_PYTHON_URL`、`KEEP_ANDROID_ENV`、`LOG_LEVEL`、
   `PROGRESS_INTERVAL`、`SHOW_RATE`）始终优先于 YAML 内的同名键
5. 内置默认值

显式传入但不存在的 `--config` 会报错；把 `BACKUP_CONFIG_FILE` 设为空或不存在路径，则不加载
默认 YAML，适合自动化或完全用环境变量。YAML 只使用顶层 `key: value`（字符串可用单引号/双引号，
布尔可用 `true/false/yes/no/on/off`）；解析由 `backup.py` 内置完成，不需要 PyYAML。

## YAML 键

> 随仓库模板 `backup-android.example.yaml` 的推荐默认是 `source_mode: device-python`；此时
> `download_device_python` 缺省即为 `true`（首跑联网下载并缓存解释器）。下表“默认”是键未
> 配置时的内置值（内置 `source_mode` 为 `host-adb`；`download_device_python` 仅在
> `device-python` 模式下默认 `true`，否则 `false`）。

| 键 | 默认 | 说明 |
|---|---|---|
| `adb` | `adb` | `adb` 可执行文件或绝对路径 |
| `host` | 空 → 不走无线 | 无线端点：`IP` 或 `IP:端口`；非空即自动 `adb connect`（别名 `address`/`ip`） |
| `device_id` | 空 → 自动选择 | ADB 设备 ID（`adb devices` 第一列，如 `AERF6R4517018096`、`adb-…._adb-tls-connect._tcp`）；留空自动选（单设备直连；多设备交互选择，非交互报错）。旧名 `device`/`adb_serial`/`serial` 兼容 |
| `source_dir` | `/sdcard/DCIM` | 设备上要备份的绝对目录 |
| `out` | 空 → 当前目录 `backup.tar.<后缀>` | 输出目标：目录（自动按 source_dir 尾部命名）或文件名，见下方“OUT 语义” |
| `compress` | 空/缺省 → `none` | `xz`、`gzip`、`zstd` 或 `none`；为空/不存在时视为 `none`（不压缩） |
| `source_mode` | `host-adb` | `host-adb`（主机逐条读）或 `device-python`（设备端打包） |
| `device_python` | 未设置 | `device-python` 用：本机 Android ARM64 Python——单文件解释器，或含 `bin/`+`lib/` 的 prefix 目录 |
| `download_device_python` | `device-python` 时 `true`（否则 `false`） | 无可用 `DEVICE_PYTHON` 时自动下载到主机缓存 |
| `device_python_url` | 固定上游 `.tar.zst` | 覆盖下载地址；可为本地 `.tar.zst` 文件路径（离线复用） |
| `keep_android_env` | 未设置 | `device-python` 保留设备端解释器缓存：`true`/`false`，未设置则交互询问、非交互删除 |
| `log_level` | `info` | `quiet`、`error`、`warn`、`info`、`debug`、`trace` |
| `progress_interval` | `5` | 进度输出最小间隔（秒），最小 `0.1` |
| `show_rate` | `false` | 在定期进度行显示有效载荷速率 |

**OUT 语义**：留空 → 当前目录的 `backup.tar.<后缀>`（后缀依 `compress` 为
`.tar.xz`/`.tar.gz`/`.tar.zst`/`.tar`）。`out` 指向**已存在的目录**、或以 `/`（或 `\`）
结尾时视为目录，写入 `<该目录>/<source_dir 尾部名><后缀>`（例如 `source_dir` 为
`/storage/emulated/0/DCIM` 且 `out: ./backups/` → `./backups/DCIM.tar.xz`）。其余按
**文件名**处理：名字已以理论后缀结尾则原样使用；否则视为“后缀不符”——非交互（无
TTY，或 `log_level` 为 `quiet`/`error`）直接按原文件名写入，交互终端询问一次是否自动
追加该后缀（回车 = 追加，输入 `n`/`no` 则按原名写入）。

**设备选择**：`host` 管无线端点（`IP` 或 `IP:端口`），非空即先执行 `adb connect`；随后优先
用 `device_id` 固定设备，未给出 `device_id` 时按 `adb devices` 中与 `host` 匹配（相同，或
`IP:` 前缀，兼容只填 IP）的在线设备，匹配不到则报错退出（不会静默改用其他设备）。
`device_id` 非空且 `host` 为空时直接固定该 ADB 设备 ID（USB serial 或 mDNS ID），不触发
`adb connect`。两者都留空 → 枚举 `adb devices`：恰一台在线设备直接使用；多台在交互终端
列出并让用户选择序号（非交互或 `log_level` 为 `quiet`/`error` 时报错退出）；0 台报错。

## 环境变量

与 YAML 键同名、语义相同（`DEVICE_PYTHON_URL`↔`device_python_url` 等）。另有两个选择类变量：

| 变量 | 说明 |
|---|---|
| `PYTHON` | Windows 上包装脚本使用的 Python 解释器完整路径（可选） |
| `BACKUP_CONFIG_FILE` | UTF-8 配置文件路径；空值/不存在则不加载 YAML |

旧环境变量 `DEVICE`/`ADB_SERIAL` 仍视为 `DEVICE_ID` 的别名；`ADB_CONNECT` 已废弃、被忽略。

## 命令行

所有归档字节走二进制 stdin/stdout；Windows 用 `cmd.exe` 的管道与重定向（避免 PowerShell
文本管道）。三个 Python 入口都支持 `--version`。

| 入口 | 用法 | 行为 |
|---|---|---|
| 本机打包 | `paxck.py create DIRECTORY` | 把 `DIRECTORY` 作为根目录写裸 PAX tar 到 stdout；普通文件带 `PAXCK.checksum.sha256` |
| 压缩 | `paxck.py compress {xz,gzip,zstd,none}` | stdin→stdout；`xz`/`gzip`/`none` 只用标准库 |
| 校验 | `paxck.py verify [ARCHIVE]` 或 `-i ARCHIVE`，可加 `-q` | 自动识别裸 tar/xz/gzip/zstd，逐普通文件校验 PAX SHA-256 |
| 提取 | `paxck.py extract [ARCHIVE] -C DEST` 或 `-i ARCHIVE` | 默认：校验后暂存原子发布，`DEST` 须不存在；`--direct-tarfile` 为可信归档直接模式 |
| Android 源适配器 | `adb_source.py [--adb ADB] [--log-level LEVEL] [--progress-interval SECONDS] DIRECTORY` | `host-adb`：经 `adb exec-out` 写裸 PAX tar 到 stdout |
| Android 主控 | `backup.py [--config PATH] [--log-level …] [--progress-interval …] [--show-rate] [--clean-env] [--clean-host-cache]` | 读取配置、组合源与压缩器、校验 `.partial` 后原子替换；`--clean-*` 只清理缓存后退出 |
| 平台包装 | `backup-android.sh [ARGS...]`；`backup-android.bat [ARGS...]` | 把所有参数转发给同目录 `backup.py` |

### stdout / stderr 职责

凡承载**数据**（归档字节）的命令，其 stdout 只写二进制数据、绝不混入文本；人类可读的
状态与诊断一律走 stderr，结果以退出码表达。纯管理类命令则用 stdout 输出状态文本。

| 命令 | stdout | stderr |
|---|---|---|
| `paxck.py create` | 仅二进制裸 tar（经 `sys.stdout.buffer`） | `[WARN]`、`[错误]` |
| `paxck.py compress` | 仅二进制压缩流 | `[错误]`（如缺少 zstd 支持） |
| `paxck.py verify` | 空（刻意不留任何文本） | 全部诊断与汇总——“共 N 个条目：…”，即使通过也写 stderr |
| `paxck.py extract`（默认） | 成功时 `[完成] 已验证并提取到 …` | `[FAIL] …`、`[WARN] …` |
| `paxck.py extract --direct-tarfile` | 成功时 `[完成] …（未校验 PAX SHA-256，非原子）` | `[FAIL] …` |
| `adb_source.py` | 仅二进制裸 tar | 进度、`[WARN]`、`[错误]` |
| `backup.py` | 文本状态（`[1/3]`…`[完成]`、`[缓存]`、`[清理]`） | 源侧进度、设备端诊断/警告、`[错误]` |
| `backup-android.sh/.bat` | 透传 `backup.py` 的 stdout | 透传 `backup.py` 的 stderr |

约定细则：

- 结果以退出码为准，不要靠解析 stdout。`paxck.py verify` 校验失败以非零退出；普通文件全部
  缺少 SHA-256 记录的第三方 tar 会被拒绝（并说明原因）。
- 把 `create`/`compress`/`verify` 当作管道阶段时，stdout 只有数据（`verify` 为空）；人类
  信息全部从 stderr 取，避免污染数据流。
- `extract` 没有二进制输出，成功文本因此放 stdout，可直接当普通命令看；失败诊断仍只在
  stderr。默认提取成功显示“已验证并提取”，直接模式明确显示“未校验 PAX SHA-256，非原子”。
- `backup.py` 的 stdout 是给人看的进度文本；备份字节写入 `out` 文件，不经 stdout。
- Windows 下用 `cmd.exe` 的二进制管道/重定向，避免 PowerShell 文本管道改写字节。

## 缓存与清理

### 主机下载缓存（自动）
`download_device_python: true` 且缺少可用解释器时，`backup.py` 从
[python-build-standalone](https://github.com/astral-sh/python-build-standalone) 固定 Release
下载 `.tar.zst` 到缓存（Windows `%LOCALAPPDATA%\andbackup`，POSIX `~/.cache/andbackup`），
只解出 `bin/python3.x` + `lib/python3.x`（跳过符号链接与 `share/`，兼容 Windows）。之后每次
先检查缓存布局，有效即复用、不再联网。解压只用标准库 `compression.zstd`（3.14+）、外部
`zstd`、或系统 `tar`（Windows `bsdtar`），不引入第三方 Python 包。

### Android 端环境缓存（device-python）
解释器会先放置到设备固定目录 `/data/local/tmp/andbackup-pyenv`（内含 `bin/`、`lib/`、
`paxck.py`、`stamp`）。每次打包前会做“正确性检查”：目录布局 + `stamp`（解释器标识 +
本机 `paxck.py` SHA-256）匹配 + 解释器可执行 `--version`。匹配则复用（不上传、不询问）；
不匹配则整体重传，并以 `--version` 自检；若自检失败（通常为损坏/不兼容上传）会自动重走放置流程。

打包完成后：
- 若本次是新建环境，交互终端会询问是否保留；回车=保留。`keep_android_env: true` 强制保留，
  `false` 强制删除；非交互（无 TTY）且未设置时默认删除，避免脚本静默留下约 230 MiB。
- 若本次复用了已存在环境，则不询问、不删除。
- 每次运行的状态文件放 `<env>/run/<uuid>`，无论保留与否都会删除，避免污染缓存。
- 解释器本身无法执行导致的失败会删除缓存并明确报错，不会悄悄回退到 `host-adb`。

清理命令（互不影响）：
```sh
src/backup-android.sh  --clean-env          # 删除设备端 /data/local/tmp/andbackup-pyenv
src/backup-android.sh  --clean-host-cache   # 删除主机下载缓存目录
```
```bat
src\backup-android.bat --clean-env
src\backup-android.bat --clean-host-cache
```

## 压缩说明
- `xz`、`gzip`、裸 tar：只用 Python 标准库（`lzma`/`gzip`），任何平台无需外部命令。
- `zstd`：Python 3.14+ 用标准库 `compression.zstd`；3.12/3.13 需主机 `PATH` 中的 `zstd`，
  否则以退出码 2 失败。
- xz 是 CPU 密集的默认档（`preset=6`）；追求速度可用 `none` 或 `gzip`。
