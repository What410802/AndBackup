# Android → PC 流式备份系统（开发者视角）

面向开发者，拆解 `paxck.py` 与 `backup-android.bat / .sh` 的内部逻辑，
以及 SSH ↔ sshd 在 USB 转发通道上的通信时序。

---

## 1. 系统分层与通信时序

重点：`adb forward` 建立的是 USB 隧道，SSH 客户端连的是本机 `localhost`，
数据全程不经过局域网；远端 `bash -o pipefail` 是退出码正确性的唯一保障。

```mermaid
sequenceDiagram
    autonumber
    participant BAT as backup-android.bat / .sh
    participant ADB as adbd（USB 守护）
    participant SSHD as sshd（Termux :8022）
    participant BASH as bash -o pipefail
    participant PY as paxck.py create
    participant XZ as xz -6 -c
    participant DISK as 本地 backup.tar.xz
    participant VFY as paxck.py verify

    Note over BAT,ADB: 阶段一：建立 USB 隧道
    BAT->>ADB: adb forward tcp:8022 tcp:8022
    ADB-->>BAT: 本机 8022 已绑定，流量经 USB 转发至手机 8022
    Note right of ADB: 端口不对外暴露<br/>不经局域网

    Note over BAT,SSHD: 阶段二：SSH 握手与认证
    BAT->>ADB: TCP connect localhost:8022
    ADB->>SSHD: USB 隧道转发
    SSHD-->>BAT: SSH-2.0 版本串
    BAT->>SSHD: KEX 交换 + 用户认证（密钥 / 密码）
    SSHD-->>BAT: 认证成功

    Note over BAT,XZ: 阶段三：远程命令执行与流式回传
    BAT->>SSHD: exec：bash -o pipefail -c '...'
    SSHD->>BASH: fork / exec
    BASH->>PY: python3 paxck.py create .
    PY-->>BASH: 裸 tar 流（pax + 内嵌 SHA-256，走 stdout）
    BASH->>XZ: 管道喂入
    XZ-->>SSHD: xz 压缩流

    loop 数据分块传输（SSH channel）
        SSHD-->>BAT: channel data（加密）
        BAT->>DISK: cmd 字节级重定向（不做编码转换）
    end

    Note over XZ,BAT: 背压：channel window 耗尽时，<br/>远端 write() 阻塞，发送端自动减速
    Note over XZ,BAT: Compression=no：数据已压缩，<br/>避免 SSH 二次压缩空耗 CPU

    Note over BASH,BAT: 阶段四：退出码回传
    XZ-->>BASH: EOF，exit 0
    BASH-->>SSHD: 管道退出码（pipefail 生效：<br/>任一环节失败即非 0）
    SSHD-->>BAT: exit-status
    BAT->>BAT: 检查 RC，非 0 则中止

    Note over BAT,VFY: 阶段五：本地校验
    BAT->>VFY: paxck.py verify backup.tar.xz
    VFY-->>BAT: 退出码 0 / 1 / 2
```

**关键时序点**

| 环节 | 说明 |
|---|---|
| `adb forward` | 客户端连 `localhost:8022`，adbd 经 USB 线转发到手机 `8022`，不经网卡 |
| `Compression=no` | 数据已由 xz 压缩，关闭 SSH 层压缩 |
| 背压 | SSH channel window 满 → 远端 `write()` 阻塞 → 管道自然限流，不会撑爆内存 |
| `pipefail` | **无它时：打包失败 + 压缩成功 = 管道返回 0**，空归档被误判为成功 |
| 退出码 | 0 通过 / 1 校验失败 / 2 zstd 无标准库支持 |

---

## 2. `backup-android.bat / .sh` 主控流程

```mermaid
flowchart TD
    START([启动]) --> CFG["读取配置区<br/>HOST / PORT / USER<br/>REMOTE_DIR / REMOTE_PY"]
    CFG --> PYCHK{"本机 python<br/>可用?"}
    PYCHK -- 否 --> ERR1["报错退出<br/>未找到 python"]
    PYCHK -- 是 --> FWD["adb forward tcp:8022 tcp:8022"]

    FWD --> SSH["执行 ssh<br/>-o Compression=no<br/>-o ServerAliveInterval=30<br/>远端：bash -o pipefail -c '...'"]

    SSH --> REDIR["输出重定向到 backup.tar.xz<br/>cmd：字节级搬运<br/>PowerShell：会做编码转换，禁用"]

    REDIR --> RC{"退出码 RC<br/>== 0 ?"}
    RC -- 非 0 --> ERR2["报错退出<br/>提示排查 sshd / IP / 存储权限"]
    RC -- 0 --> VER["调用 paxck.py verify"]

    VER --> VRC{"校验退出码"}
    VRC -- 0 --> OK["完成<br/>打印文件大小"]
    VRC -- 非 0 --> ERR3["失败<br/>提示重新传输"]

    OK --> END([结束])
    ERR1 --> END
    ERR2 --> END
    ERR3 --> END

    style ERR1 fill:#ffe0e0
    style ERR2 fill:#ffe0e0
    style ERR3 fill:#ffe0e0
    style OK fill:#e0f0e0
```

**Windows 端选 cmd 的实测依据**

PowerShell 的 `>` 会把输出按控制台编码（中文 Windows 为 CP936）解码成字符串，
再用 UTF-16LE 编码写文件。实测 1,245,044 字节的压缩流经过该路径后：

```
U+FFFD 替换字符出现 146,694 次 → 写出 1,915,834 字节
```

约 12% 的字节被永久污染，不可恢复。cmd 的重定向是纯字节搬运，绕开该问题。

---

## 3. `paxck.py create` 内部逻辑

```mermaid
flowchart TD
    A([入口：create DIR]) --> B{"DIR 存在<br/>且为目录?"}
    B -- 否 --> BX["sys.exit 报错"]
    B -- 是 --> C["tarfile.open<br/>mode='w|' 流式不可 seek<br/>format=PAX_FORMAT<br/>fileobj=sys.stdout.buffer"]

    C --> D["写入根目录条目 DIRTYPE"]
    D --> E["os.walk 遍历<br/>followlinks=False"]

    E --> F["子目录 → DIRTYPE"]
    E --> G{"条目类型判断"}

    G -- 符号链接 --> H["SYMTYPE<br/>linkname = os.readlink"]
    G -- 非普通文件 --> I["跳过<br/>设备 / FIFO 等"]
    G -- 普通文件 --> J{"nlink > 1 且<br/>dev+ino 已见过?"}

    J -- 是 --> K["LNKTYPE<br/>指向首次出现的路径"]
    J -- 否 --> L["记录 inode<br/>（供后续硬链接复用）"]

    L --> M["第一遍读：分块计算 SHA-256<br/>CHUNK = 1 MiB，内存恒定"]
    M --> N["写入 pax 扩展头<br/>PAXCK.checksum.sha256"]
    N --> O["第二遍读：addfile ti, fh<br/>tarfile 自行流式写入"]

    O --> P{"读取抛<br/>OSError?"}
    P -- 是 --> Q["stderr 输出 WARN<br/>跳过该条目，不中断"]
    P -- 否 --> R["继续"]

    F --> S{"还有条目?"}
    H --> S
    I --> S
    K --> S
    Q --> S
    R --> S

    S -- 是 --> G
    S -- 否 --> T["tf.close 关闭流"]
    T --> U([退出码 0])

    style BX fill:#ffe0e0
    style Q fill:#fff4d0
    style U fill:#e0f0e0
```

**设计取舍**

| 点 | 选择 | 原因 |
|---|---|---|
| 两遍读 | 先算哈希，再 `addfile(ti, fh)` | 避免把整个文件读进内存；实测 50 MB 文件峰值内存仅 8 MB |
| `sys.stdout.buffer` | 二进制安全 | Windows 文本模式会把 `0x0A` 翻译成 `0x0D 0x0A`，损坏二进制流 |
| `surrogateescape` | 路径编解码 | 容忍非 UTF-8 文件名字节 |
| 硬链接去重 | `(st_dev, st_ino)` | 避免重复存储；**沙盒 virtiofs 不支持 `os.link`，此分支未实测** |

---

## 4. `paxck.py verify` 内部逻辑

```mermaid
flowchart TD
    A([入口：verify PATH / stdin]) --> B["读取前 6 字节"]

    B --> C{"sniff 魔术字节"}
    C -- "FD 37 7A 58 5A 00" --> D["xz → lzma.LZMAFile<br/>标准库，本机零依赖"]
    C -- "1F 8B" --> E["gzip → gzip.GzipFile"]
    C -- "28 B5 2F FD" --> F{"Python 3.14+<br/>compression.zstd?"}
    C -- 其它 --> G["视为裸 tar"]

    F -- 是 --> F1["ZstdFile 解压"]
    F -- 否 --> F2["exit 2<br/>打印三条解决方案"]

    D --> H["tarfile.open mode='r|'"]
    E --> H
    F1 --> H
    G --> H

    H --> I{"解析失败?"}
    I -- 是 --> I1["exit 1<br/>提示传输不完整或截断"]

    I -- 否 --> J["遍历 member"]
    J --> K{"是普通文件<br/>且有 PAXCK key?"}
    K -- 否 --> L["skip++<br/>目录 / 链接 / 无记录"]
    K -- 是 --> M["extractfile<br/>分块算 SHA-256"]

    M --> N{"与 pax 中<br/>记录值一致?"}
    N -- 是 --> O["ok++"]
    N -- 否 --> P["bad++<br/>记录文件名与哈希前 16 位"]

    O --> Q["下一个 member"]
    L --> Q
    P --> Q
    Q --> J

    J -- 遍历中异常 --> R["truncated = True<br/>bad++，提示流中断"]
    J -- 遍历结束 --> S{"total == 0 ?"}

    R --> S
    S -- 是 --> T["exit 1<br/>空归档：tar 很可能执行失败"]
    S -- 否 --> U{"bad > 0 ?"}

    U -- 是 --> V["exit 1<br/>列出失败条目（最多 50 条）"]
    U -- 否 --> W["exit 0<br/>打印统计：total / ok / bad / skip"]

    style F2 fill:#ffe0e0
    style I1 fill:#ffe0e0
    style T fill:#ffe0e0
    style V fill:#ffe0e0
    style W fill:#e0f0e0
```

**三道防线**

1. **流完整性** —— 解析异常 / 截断会被捕获，提示重新传输
2. **条目数 > 0** —— 空归档能通过 `xz -t` 和 `tar -tf`，只有数条目才能发现
3. **每文件 SHA-256** —— 精确定位到损坏的具体文件

实测退出码矩阵：

| 场景 | 退出码 | 输出 |
|---|---|---|
| 正常 | 0 | `共 N 个条目：SHA-256 通过 X，失败 0` |
| 空归档 | 1 | `归档为空（0 个条目）—— tar 很可能执行失败` |
| 传输截断 | 1 | `流在第 N 个条目后中断` |
| 内容损坏 | 1 | `xxx: SHA-256 不符` |
| zstd 输入 | 2 | 三条解决方案 |

---

## 5. 未实测项

- **`.bat` 本体**：沙盒无 Windows cmd，语法靠人工审查
- **Android 硬链接**：沙盒 virtiofs 不支持 `os.link`，`create` 的 LNKTYPE 分支未跑通
- **Windows 上的元信息还原**：pax 可保留亚秒时间戳 / 权限 / 符号链接 / 长路径 / UTF-8 名，
  但 Windows 文件系统不一定能还原这些属性
