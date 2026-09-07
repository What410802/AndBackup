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

Windows 在 `cmd.exe` 中编辑 `src\backup-android.bat` 顶部的 `ADB`、`SOURCE_DIR`、
`OUT` 和 `COMPRESS`，然后运行它。不要把命令改在 PowerShell 的 `>` 重定向中执行；
压缩流必须按字节写入。

Linux 主控可配置的环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ADB` | `adb` | `adb` 可执行文件或绝对路径 |
| `SOURCE_DIR` | `/sdcard/DCIM` | 设备上的绝对目录 |
| `OUT` | 随压缩类型决定 | 主机端归档路径 |
| `COMPRESS` | `xz` | `xz`、`gzip`、`zstd` 或 `none` |

`xz` 与 `gzip` 只用 Python 标准库。`zstd` 需要 Python 3.14+ 的
`compression.zstd`，或主机 `PATH` 中的 `zstd`；缺少时会以退出码 2 失败。

## 工作方式

主控调用一个主机端进程：

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
  backup-android.sh        Linux 主控
  backup-android.bat       Windows cmd 主控
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
- Windows `cmd` 主控尚未在 CI 自动化验证，Linux 端与 Python 核心已有自动化测试。

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

完整测试说明见 [docs/testing.md](docs/testing.md)，设计说明见
[docs/flow.md](docs/flow.md)。
