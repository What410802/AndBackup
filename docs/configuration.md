# 配置与命令行参考

[English version](configuration.en.md) | [中文 README](../README.md) | [数据流与边界](flow.md)

本项目把用户面用法放在 [README](../README.md)，把完整配置、命令行与缓存清理细节集中在本页。

## 配置来源与优先级

1. 命令行 `--config PATH`（优先级最高）
2. 环境变量 `BACKUP_CONFIG_FILE`
3. 脚本同目录默认 `src/backup-android.yaml`（存在时）
4. 业务环境变量（`ADB`、`HOST`、`SERIAL`、`ANDROID_SERIAL`、`SOURCE_DIR`、`OUT`、`COMPRESS`、`SOURCE_MODE`、`DEVICE_PYTHON`、
   `DOWNLOAD_DEVICE_PYTHON`、`DEVICE_PYTHON_URL`、`KEEP_ANDROID_ENV`、`LOG_LEVEL`、
   `PROGRESS_INTERVAL`、`SHOW_RATE`、`FORCE`）始终优先于 YAML 内的同名键
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
| `serial` | 空 → 自动选择 | ADB 序列号（`adb devices` 第一列，等价 `adb -s SERIAL`），如 `AERF6R4517018096`、`adb-…._adb-tls-connect._tcp`；留空自动选（单设备直连；多设备交互选择，非交互报错）。`-t` 传输 ID 仅在序列号重复时才需要。旧名 `device_id`/`device`/`adb_serial` 兼容 |
| `source_dir` | `/sdcard/DCIM` | 设备上要备份的绝对目录 |
| `out` | 空 → 当前目录 `backup.tar.<后缀>` | 输出目标：目录（自动按 source_dir 尾部命名）或文件名，见下方“OUT 语义” |
| `compress` | 空/缺省 → `none` | `xz`、`gzip`、`zstd` 或 `none`；为空/不存在时视为 `none`（不压缩） |
| `source_mode` | `host-adb` | `host-adb`（主机逐条读）或 `device-python`（设备端打包） |
| `device_python` | 未设置 | `device-python` 用：本机 Android ARM64 Python——单文件解释器，或含 `bin/`+`lib/` 的 prefix 目录 |
| `download_device_python` | `device-python` 时 `true`（否则 `false`） | 无可用 `DEVICE_PYTHON` 时自动下载到主机缓存 |
| `device_python_url` | 固定上游 `.tar.zst` | 覆盖下载地址；可为本地 `.tar.zst` 文件路径（离线复用） |
| `keep_android_env` | 未设置 | `device-python` 保留设备端解释器缓存：`true`/`false`，未设置则交互询问、非交互删除 |
| `log_level` | `info` | `quiet`、`error`、`warn`、`info`、`debug`、`trace` |
| `force` | `false` | 目标文件已存在时直接覆写，不再询问（别名 `overwrite`；等同命令行 `-f`/`--force`） |
| `progress_interval` | `5` | 进度输出最小间隔（秒），最小 `0.1` |
| `show_rate` | `false` | 在定期进度行显示有效载荷速率 |

**OUT 语义**：留空 → 当前目录的 `backup.tar.<后缀>`（后缀依 `compress` 为
`.tar.xz`/`.tar.gz`/`.tar.zst`/`.tar`）。`out` 指向**已存在的目录**、或以 `/`（或 `\`）
结尾时视为目录，写入 `<该目录>/<source_dir 尾部名><后缀>`（例如 `source_dir` 为
`/storage/emulated/0/DCIM` 且 `out: ./backups/` → `./backups/DCIM.tar.xz`）。其余按
**文件名**处理：名字已以理论后缀结尾则原样使用；否则视为“后缀不符”——非交互（无
TTY，或 `log_level` 为 `quiet`/`error`）直接按原文件名写入，交互终端询问一次是否自动
追加该后缀（回车 = 追加，输入 `n`/`no` 则按原名写入）。

**输出目标预检**：在任何 ADB/设备操作之前，先在目标同目录创建占位文件
（`<目标名>.partial.<随机>`），用它验证“父目录能创建、能写”，并顺带体检目标本身。因此以下
情况都会**立刻失败**，不会白传一遍：“目标已存在且是目录”“目标不是普通文件（管道/设备文件）”
“路径里某一段是已存在的文件”“父目录不可写”，以及 Windows 上“目标只读或被其他程序占用”。
交互终端会提示改输新的 `out`（回车放弃并按失败退出）；非交互或 `log_level` 为
`quiet`/`error` 时直接报错。万一传输与校验都成功、最后改名到目标却失败（竞态），校验过的
归档会**保留**在该 `.partial.*` 文件里并打印其路径，可手动改名/移动，不会随失败一起删除。

**已存在的目标文件**：目标文件（含后缀）已存在时不会静默覆盖：交互终端先问
“覆盖它吗？[y/N]”（默认不覆盖，答 `n` 或回车可改输另一个路径），非交互环境（无控制台，
或 `log_level` 为 `quiet`/`error`）直接报错退出并提示加 `--force`，以免无人值守时覆盖掉
上一次的备份。加命令行 `-f`/`--force`（或配置 `force: true`）则跳过询问直接覆写；它只决定
“是否询问”，并不会绕过上面那些真的写不进去的情况。

**相对路径基准**：所有相对路径都相对 **启动进程的工作目录**（你调用包装脚本或 `backup.py`
时所在的目录），不是配置文件所在目录，也不是 `src/` 脚本目录：`out`、`device_python`、
`device_python_url` 指向的本地 `.tar.zst`、`BACKUP_CONFIG_FILE` 与 `--config` 都是这样；
`out` 的父目录不存在时会自动创建（`backup.py` 会先 `os.makedirs`）。唯一例外是默认配置
文件：未指定 `--config`/`BACKUP_CONFIG_FILE` 时，按 `backup.py` 所在目录查找
`backup-android.yaml`。

**设备选择**：`host` 管无线端点（`IP` 或 `IP:端口`），非空即先执行 `adb connect`；随后优先
用 `serial` 固定设备，未给出 `serial` 时按 `adb devices` 中与 `host` 匹配（相同，或
`IP:` 前缀，兼容只填 IP）的在线设备，匹配不到则报错退出（不会静默改用其他设备）。
`serial` 非空且 `host` 为空时直接固定该 ADB 序列号（USB serial 或 mDNS id），不触发
`adb connect`。两者都留空 → 枚举 `adb devices`：恰一台在线设备直接使用；多台在交互终端
列出并让用户选择序号（非交互或 `log_level` 为 `quiet`/`error` 时报错退出）；0 台报错。

## 环境变量

**语言**：命令行输出默认按环境自动选择语言，可用 `--lang zh|en|auto`（`paxck.py`、
`adb_source.py`、`backup.py` 都接受；`paxck.py` 的位置在子命令前后皆可）或
`ANDROBACKUP_LANG=zh|en` 强制。解析顺序为 `--lang` → `ANDROBACKUP_LANG` →
`LC_ALL`/`LC_MESSAGES`/`LANGUAGE`/`LANG` → 系统区域设置 → 回退 `en`。主控会把选中的
语言导出给子进程（即 `backup.py` 调用 `adb_source.py`/`paxck.py` 时语言一致）。
`--help`/错误提示文本同样随之切换；`argparse` 自身的 `usage:`/`error:` 前缀保持英文。

Windows 上的“系统区域设置”取的是**用户界面语言**（Win32 的 UI language），而不是
`locale.getlocale()`：后者描述的是进程 C 区域，UTF-8 模式会把中文系统报成英文，于是英文
模板里会嵌进中文的系统错误文本。系统错误的措辞同样由本工具自己按 `errno` 翻译
（`i18n.os_error`：文件已存在/权限不足或被占用/路径某段不是目录/只读文件系统……），只有
认不出的 `errno` 才原样保留系统文本，因此一条消息不会中英夹杂。

与 YAML 键同名、语义相同（`DEVICE_PYTHON_URL`↔`device_python_url` 等）。另有两个选择类变量：

| 变量 | 说明 |
|---|---|
| `PYTHON` | Windows 上包装脚本使用的 Python 解释器完整路径（可选） |
| `BACKUP_CONFIG_FILE` | UTF-8 配置文件路径；空值/不存在则不加载 YAML |

旧环境变量 `DEVICE_ID`/`DEVICE`/`ADB_SERIAL` 仍视为 `SERIAL` 的别名（`ANDROID_SERIAL` 也接受，与 adb 官方一致）；`ADB_CONNECT` 已废弃、被忽略。

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
| Android 主控 | `backup.py [--config PATH] [--log-level …] [--progress-interval …] [--show-rate] [-f|--force] [--clean-env] [--clean-host-cache] [--list-tree] [--tree-out PATH] [--prune-source] [--prune-dry-run]` | 读取配置、组合源与压缩器、校验 `.partial` 后原子替换；`--clean-*` 只清理缓存后退出；`--list-tree` 只列出源目录的详细信息树（模式、属主/组（数字 UID/GID）、大小、时间、符号链接目标），默认写 stdout，`--tree-out PATH` 写入文件。每个条目一次 adb 调用，大树较慢；它是诊断命令，不写入归档；`--prune-source` 在归档发布后删除已打包的源条目（见下） |
| 平台包装 | `backup-android.sh [ARGS...]`；`backup-android.bat [ARGS...]` | 把所有参数转发给同目录 `backup.py` |

### 删除已打包的源条目（`--prune-source`）

`backup.py --prune-source` 在**归档通过校验并原子发布之后**，把源目录下确实写进归档的
条目从设备上删除，用于释放空间。它是纯粹的释放空间操作，不可撤销：

- **仅命令行选项**：不提供 YAML 键，也不从环境变量读取（配置文件里写了也无效），避免
  一次性配置后每次备份都静默删源。
- **只删已打包的条目**：打包器会把每个条目的结果写成清单（`P:` 普通条目、`D:` 目录、
  `S:` 被跳过、`L:` 枚举不完整）；适配器因权限、scoped storage、非普通文件或“打包期间
  被修改”而跳过的条目**永远不删**。
- **源根目录永不删除**（例如 `/storage/emulated/0/Android/data/com.tencent.mobileqq` 本身
  会保留，即使里面的条目都被删除）。
- **目录条件更严**：仅当该目录的枚举完整、且其下没有任何被跳过的条目时才删除；枚举不完整
  （`find` 返回非零）时**只删普通文件与符号链接，保留全部目录**。
- **目录用 `rmdir` 而非 `rm -rf`**：若枚举之后应用又写了新文件，该目录删除失败并被保留，
  绝不会递归误删。
- 文件用 `rm -f`、目录用 `rmdir`，按路径深度由深到浅分批执行；每个批次失败时会逐个重试
  以定位具体条目，失败的条目以 `[WARN]` 报出。
- 删除顺序：先删文件，再删空目录；删除完成后以 `[INFO]` 汇总“删除 N 个（其中目录 M 个），
  保留 K 个未打包或未能删除的条目”。
- `--prune-dry-run` 只列出将删除的条目和数量，不做任何删除（必须与 `--prune-source` 同用）。
- 交互终端（且 `log_level` 不是 `quiet`/`error`）会在删除前打印计划并询问一次；回车或 `y`
  继续，`n` 取消。非交互（无 TTY）视为已授权——`--prune-source` 本身就是授权信号。
- `backup.py` 与 `adb_source.py`（以及 `device-python` 模式下设备端 `paxck.py create`）
  都支持 `--packed-manifest PATH`；它只在使用 prune 时由主控自动传递，正常使用无需手工调用。
- 备份失败、校验失败或未发布时**不会**删除任何源条目；失败清理逻辑只移除主机端临时文件。

示例：
```sh
src/backup-android.sh --prune-source --prune-dry-run   # 先看会删什么
src/backup-android.sh --prune-source                   # 归档发布后真删
```

### stdout / stderr 职责

凡承载**数据**（归档字节）的命令，其 stdout 只写二进制数据、绝不混入文本；人类可读的
状态与诊断一律走 stderr，结果以退出码表达。纯管理类命令则用 stdout 输出状态文本。

| 命令 | stdout | stderr |
|---|---|---|
| `paxck.py create` | 仅二进制裸 tar（经 `sys.stdout.buffer`） | `[WARN]`、`[ERROR]` |
| `paxck.py compress` | 仅二进制压缩流 | `[ERROR]`（如缺少 zstd 支持） |
| `paxck.py verify` | 空（刻意不留任何文本） | 全部诊断与汇总——“共 N 个条目：…”，即使通过也写 stderr |
| `paxck.py extract`（默认） | 成功时 `[DONE] 已验证并提取到 …` | `[FAIL] …`、`[WARN] …` |
| `paxck.py extract --direct-tarfile` | 成功时 `[DONE] …（未校验 PAX SHA-256，非原子）` | `[FAIL] …` |
| `adb_source.py` | 仅二进制裸 tar | `[PROGRESS]` 进度、`[WARN]`、`[ERROR]` |
| `backup.py` | 文本状态（`[1/3]`…`[DONE]`、`[CACHE]`、`[CLEAN]`） | 源侧进度、设备端诊断/警告、`[ERROR]` |
| `backup-android.sh/.bat` | 透传 `backup.py` 的 stdout | 透传 `backup.py` 的 stderr |

约定细则：

- 状态标签（`[ERROR]`/`[WARN]`/`[DONE]`/`[INFO]`/`[PROGRESS]`/`[DEBUG]`/`[FAIL]`/
  `[CACHE]`/`[CLEAN]`/`[DOWNLOAD]`）**不翻译**，只翻译其后的正文；这样脚本与测试可以
  在任意语言下匹配同一个标签（历史上中文输出用 `[错误]`/`[完成]`/`[进度]`，现已统一）。

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
设备端脚本 `paxck.py` + `i18n.py`、`stamp`）。每次打包前会做“正确性检查”：目录布局 +
`stamp`（解释器标识 + 本机 `paxck.py`/`i18n.py` 的 SHA-256）匹配 + 解释器可执行
`--version`。匹配则复用（不上传、不询问）；
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
