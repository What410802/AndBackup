#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""User-facing message catalog and language selection for the AndBackup tools.

The catalog is a plain Python dict rather than a gettext/JSON data file: the
project deliberately stays stdlib-only, and ``device-python`` mode uploads this
module to the device next to ``paxck.py``.

Language selection order (first match wins):

1. ``--lang`` on the command line (``zh``/``en``/``auto``; ``auto`` = resume
   the search below);
2. ``ANDROBACKUP_LANG`` — a parent process exports this so its child tools
   (``adb_source.py``, ``paxck.py``) print the same language;
3. ``LC_ALL``, ``LC_MESSAGES``, ``LANGUAGE``, ``LANG``;
4. the OS locale — on Windows the *user interface* language, because that is
   also the language the OS writes into ``OSError.strerror``;
5. ``locale.getlocale()``, then ``locale.getdefaultlocale()``;
6. ``en``.

Message keys are dotted and grouped by module (``paxck.err.empty``,
``backup.warn.adb_missing``, ...).  Templates use ``str.format`` placeholders.
Status tags (``[ERROR]``/``[WARN]``/``[DONE]``/...) are intentionally
language-neutral so logs and tests can parse them regardless of the language.
OS-supplied error text is passed through :func:`os_error`, which prefers our
own catalog over ``strerror`` so a message never mixes two languages.
"""
import errno
import locale
import os
import sys

LANGUAGES = ('zh', 'en')
FALLBACK = 'en'
ENV_VAR = 'ANDROBACKUP_LANG'
AUTO = 'auto'

# Tags are never translated: scripts and tests match on them.
TAGS = {
    'error': '[ERROR]',
    'warn': '[WARN]',
    'done': '[DONE]',
    'info': '[INFO]',
    'progress': '[PROGRESS]',
    'debug': '[DEBUG]',
    'trace': '[TRACE]',
    'fail': '[FAIL]',
    'cache': '[CACHE]',
    'clean': '[CLEAN]',
    'download': '[DOWNLOAD]',
}


MESSAGES = {
    'zh': {
        # ---- OS errors (i18n.os_error) ------------------------------------
        'error.errno.exists': '文件或目录已存在',
        'error.errno.denied': '权限不足或被其他程序占用',
        'error.errno.not_found': '路径不存在',
        'error.errno.not_a_dir': '路径中的某一段不是目录',
        'error.errno.is_a_dir': '目标是一个目录',
        'error.errno.no_space': '设备或磁盘空间不足',
        'error.errno.read_only_fs': '文件系统是只读的',
        'error.errno.busy': '文件正被占用',
        'error.errno.name_too_long': '路径过长',
        # ---- shared command line ------------------------------------------
        'i18n.cli.lang_help':
            '界面语言：zh / en / auto（默认按环境自动检测，回退 en）',
        # ---- paxck.py: stream helpers -------------------------------------
        'paxck.err.zstd_stream_missing':
            '输入是 zstd 流，但当前 Python 无标准库 zstd 支持，PATH 里也没有 zstd。',
        'paxck.hint.install_zstd': '方案一：在主机安装 zstd',
        'paxck.hint.upgrade_python': '方案二：升级到 Python 3.14+',
        'paxck.hint.use_xz': '方案三：改用 xz 压缩（本脚本原生支持）',
        'paxck.err.zstd_pump': 'zstd 输入流读取失败：{err}',
        'paxck.err.zstd_decompress': 'zstd 解压失败：{detail}',
        # ---- paxck.py: writing one regular file ---------------------------
        'paxck.err.read_failed': '读取 {label} 失败：{err}',
        'paxck.warn.skip_read': '跳过 {label}: {err}',
        'paxck.warn.size_changed':
            '{label}: 读取前后大小不一致（{expected} -> {actual}），文件正在被修改',
        'paxck.err.abort_pack': '{message}，终止打包',
        'paxck.err.reopen_failed': '再次打开 {label} 失败：{err}',
        'paxck.err.write_source_failed': '写入 {label} 时数据源失败：{err}',
        'paxck.err.stream_unrecoverable': '归档流已不可恢复，终止打包',
        'paxck.err.short_read':
            '写入 {label} 时第二遍读少了 {count} 字节；',
        'paxck.err.source_changed_verify':
            '源文件在打包期间发生变化，归档校验将失败，终止打包',
        'paxck.err.extra_bytes': '写入 {label} 时数据源多出 {count} 字节；',
        'paxck.err.source_changed': '源文件在打包期间发生变化，终止打包',
        # ---- paxck.py: create / compress ---------------------------------
        'paxck.err.source_missing': '源目录不存在 -> {root}',
        'paxck.warn.walk_failed': '无法遍历 {name}: {err}',
        'paxck.err.unknown_compressor':
            '未知压缩类型 {kind}（可选 xz / gzip / zstd / none）',
        'paxck.err.zstd_unsupported':
            '本机没有可用的 zstd 支持（无标准库 zstd，PATH 里也没有 zstd）',
        # ---- paxck.py: verify --------------------------------------------
        'paxck.verify.unreadable': '无法读取 {path}：{err}',
        'paxck.verify.unparsable': '无法解析归档：{err}',
        'paxck.verify.truncated_hint': '（若为压缩流，通常是传输不完整/被截断）',
        'paxck.verify.entry_unreadable': '{name}: 无法读取',
        'paxck.verify.mismatch':
            '{name}: SHA-256 不符 (记录 {expected}…, 实际 {actual}…)',
        'paxck.verify.stream_broken': '流在第 {count} 个条目后中断：{err}',
        'paxck.verify.empty': '归档为空（0 个条目）—— tar 很可能执行失败',
        'paxck.verify.no_checksum_records':
            '{count} 个普通文件全部缺少 {key} 记录，无法做内容校验。',
        'paxck.verify.no_checksum_origin':
            '该归档不是由 paxck create 生成的（或被剥离了 pax 扩展头）。',
        'paxck.verify.no_regular_files': '注意：归档内没有普通文件，本次没有做内容校验',
        'paxck.verify.more_failures': '... 其余 {count} 条省略',
        'paxck.verify.incomplete_hint': '提示：归档不完整（传输中断？），请重新传输',
        'paxck.verify.summary':
            '\n共 {total} 个条目：SHA-256 通过 {ok}，失败 {bad}，无记录 {skip}',
        # ---- paxck.py: extraction safety ---------------------------------
        'paxck.extract.empty_path': '条目路径为空',
        'paxck.extract.nul_path': '条目路径含 NUL：{name}',
        'paxck.extract.unsafe_path': '条目路径不是安全的 POSIX 相对路径：{name}',
        'paxck.extract.dot_path': '条目路径含空、. 或 .. 组件：{name}',
        'paxck.extract.drive_path': '条目路径含 Windows 驱动器语法：{name}',
        'paxck.extract.escape': '条目路径越过了目标目录',
        'paxck.extract.bad_parent': '条目父路径不是已创建的真实目录：{path}',
        'paxck.extract.missing_checksum':
            '{name}: 普通文件缺少 {key}，拒绝提取未校验内容',
        'paxck.extract.no_content': '{name}: tar 无法提供文件内容',
        'paxck.extract.length_mismatch':
            '{name}: 读取长度 {size} 不等于 tar 记录的 {expected}',
        'paxck.extract.mismatch':
            '{name}: SHA-256 不符（记录 {expected}…，实际 {actual}…）',
        'paxck.err.archive_unreadable': '无法读取归档 {path}：{err}',
        'paxck.extract.duplicate': '归档含重复条目：{name}',
        'paxck.extract.link_nul': '{name}: 符号链接目标含 NUL',
        'paxck.extract.unsupported_type': '{name}: 不支持的 tar 条目类型 {type}',
        'paxck.extract.empty': '归档为空（0 个条目）',
        'paxck.extract.bad_hardlink':
            '{name}: 硬链接目标不是已提取的普通文件：{target}',
        'paxck.warn.chmod_failed': '无法恢复 {name} 的权限位：{err}',
        'paxck.warn.utime_failed': '无法恢复 {name} 的修改时间：{err}',
        # ---- paxck.py: extract commands ----------------------------------
        'paxck.extract.exists': '目标目录已存在，拒绝覆盖：{path}',
        'paxck.extract.no_parent': '目标目录的父目录不存在：{path}',
        'paxck.done.extracted': '已验证并提取到 {path}',
        'paxck.extract.refused': '拒绝提取归档：{err}',
        'paxck.extract.input_failed': '{err}',
        'paxck.extract.damaged': '归档损坏或截断，未提取：{err}',
        'paxck.extract.write_failed': '提取失败，未发布目标目录：{err}',
        'paxck.direct.not_a_dir': 'tarfile 直接提取的目标不是目录：{path}',
        'paxck.direct.mkdir_failed': '无法创建提取目标目录：{err}',
        'paxck.direct.damaged': 'tarfile 直接提取失败：归档损坏或截断：{err}',
        'paxck.direct.failed': 'tarfile 直接提取失败：{err}',
        'paxck.done.direct_extracted':
            '已由 tarfile 直接提取到 {path}（未校验 PAX SHA-256，非原子）',
        # ---- paxck.py: command line --------------------------------------
        'paxck.cli.description':
            '创建/校验带 pax 内嵌 SHA-256 的 tar 归档（流式，仅用标准库）',
        'paxck.cli.create_help': '打包本机目录到 stdout',
        'paxck.cli.compress_help': '把 stdin 压缩后写到 stdout（取代外部 xz/gzip）',
        'paxck.cli.verify_help': '校验归档（自动识别 xz/gzip）',
        'paxck.cli.extract_help': '提取归档（默认校验 SHA-256 后原子发布）',
        'paxck.cli.path_help': '归档路径；省略则从 stdin 读',
        'paxck.cli.input_help': '同位置参数，归档路径',
        'paxck.cli.directory_help': '默认模式的尚不存在目标目录；直接模式可为已有目录',
        'paxck.cli.direct_help': '直接调用 tarfile 写入目标；跳过校验和原子性，只用于可信归档',
        # ---- android_python.py ----------------------------------
        'py.err.download': '无法下载 Android Python {url}：{err}',
        'py.err.write_download': '无法写入下载文件 {path}：{err}',
        'py.err.no_zstd': '没有可用的 zstd 解压器',
        'py.err.zstd_failed': 'zstd 解压失败',
        'py.err.zstd_failed_detail': 'zstd 解压失败：{detail}',
        'py.err.no_zstd_or_tar':
            '需要 zstd 支持才能解压 Python：当前 Python 无 compression.zstd，'
            'PATH 中没有 zstd，也没有可用的 tar',
        'py.err.no_zstd_or_bsdtar': '没有可用的 zstd 解压器或 tar',
        'py.err.tar_read_failed': 'tar 无法读取归档：{detail}',
        'py.err.tar_extract_failed': 'tar 解压 Python 失败',
        'py.err.tar_extract_failed_detail': 'tar 解压 Python 失败：{detail}',
        'py.err.interpreter_missing': '归档中未找到 bin/python3* 解释器',
        'py.err.entry_unreadable': '无法读取归档条目：{name}',
        'py.err.invalid_prefix': '解压结果缺少 bin/python*，不是有效的 Python prefix',
        'py.err.no_prefix': '下载/解压后未生成有效 Python prefix：{prefix}',
        'py.err.device_python_missing': '设备 Python 不存在：{path}',
        'py.hint.point_or_download':
            '  可设置 device_python 指向已有解释器，或设 '
            'download_device_python: true 自动下载到该路径',
        'py.err.mode_needs_python': 'SOURCE_MODE=device-python 需要 Android 端 Python：',
        'py.hint.set_device_python':
            '  · 设置 device_python 指向已有的单文件解释器或 prefix 目录，或',
        'py.hint.enable_download':
            '  · 设 download_device_python: true，脚本会自动从 '
            'python-build-standalone 下载到缓存',
        'py.log.fetching': '获取 Android Python：{url}',
        'py.log.cached_archive': '使用已缓存的归档：{path}',
        'py.log.unpacking_to': '解压到：{path}',
        'py.log.done': '完成（{method}）：{path}',
        # ---- adb_source.py ---------------------------------------
        'adb.err.launch': '无法启动 adb {adb}：{err}',
        'adb.err.exec_out': 'adb exec-out {command}：{detail}',
        'adb.err.remote_rc': 'adb exec-out {command} 远端退出码 {rc}',
        'adb.err.no_status': 'adb exec-out {command} 未返回有效的远端退出码',
        'adb.err.find_not_nul':
            'adb exec-out find 输出没有 NUL 终止，拒绝解析不完整目录清单',
        'adb.err.no_paths': 'adb exec-out 未列出源目录 {root}',
        'adb.err.stat_unparsable': '无法解析 Android stat 输出 {raw}: {err}',
        'adb.err.invalid_loglevel':
            '无效日志级别：{level}（可选 quiet/error/warn/info/debug/trace）',
        'adb.progress.discovered': '已发现 {count} 个条目',
        'adb.progress.enumerating': '正在枚举 Android 目录...',
        'adb.progress.enum_label': '枚举目录',
        'adb.progress.enum_running':
            '仍在枚举目录：已发现 {count} 个条目，收到 {size} 清单数据',
        'adb.progress.enum_done':
            '目录枚举完成：发现 {count} 个条目，收到 {size} 清单数据',
        'adb.progress.entry': '条目 {done}/{total}，ADB 有效载荷 {size}：{name}{suffix}',
        'adb.progress.entry_skipped': '（跳过）',
        'adb.debug.entry': '开始处理：{name}',
        'adb.progress.transfer': '传输中，ADB 有效载荷 {size}{rate}{current}',
        'adb.progress.rate': '，速率 {rate}/s',
        'adb.progress.current': '：{name}',
        'adb.progress.finish':
            '完成：{done}/{total} 个条目，ADB 有效载荷 {size}（清单 {list}，文件内容 {files}）',
        'adb.warn.find_rc': 'find 枚举源目录时返回退出码 {rc}；无法访问的条目将被跳过',
        'adb.warn.skip_metadata': '跳过 {path}：读取元数据失败：{err}',
        'adb.warn.skip_symlink': '跳过符号链接 {name}：{err}',
        'adb.warn.skip_non_regular': '跳过非普通文件 {name}',
        'adb.warn.incomplete': '部分条目无法读取，已跳过；归档仍会照常校验并发布',
        'adb.cli.description':
            '经 adb exec-out 把 Android 目录写为 stdout 上的裸 PAX tar',
        'adb.cli.adb_help': 'adb 可执行文件路径（默认 adb）',
        'adb.cli.log_level_help': '日志级别',
        'adb.cli.progress_help': '进度输出最小间隔秒数',
        'adb.cli.show_rate_help': '在定期进度行显示 ADB 有效载荷速率',
        'adb.cli.directory_help': '设备上的非根绝对目录',
        'adb.cli.pack_help':
            '枚举设备目录并把裸 PAX tar 写到 stdout（管道中的数据源阶段）',
        'adb.err.source_root_absolute':
            'Android 源路径必须是非根绝对目录：{root}',
        'adb.err.source_not_directory': 'Android 源路径不是目录：{root}',
        'adb.err.source_list_failed': '无法枚举 Android 源目录：{err}',
        'adb.err.source_root_absolute':
            'Android 源路径必须是非根绝对目录：{root}',
        'adb.err.source_not_directory': 'Android 源路径不是目录：{root}',
        'adb.err.source_list_failed': '无法枚举 Android 源目录：{err}',
        'i18n.detail_sep': '：{detail}',
        # ---- backup.py -------------------------------------------
        'backup.err.list_devices': '无法枚举 ADB 设备',
        'backup.err.connect_failed': '无法连接 ADB 设备 {host}',
        'backup.err.host_no_match':
            '已连接 {host}，但 adb devices 中未找到对应设备。'
            '请确认无线调试端口未变化，或在配置中设置 serial。',
        'backup.err.no_devices':
            'adb 未发现已授权的设备。请确认已开启 USB 调试并在设备上授权，'
            '或在配置中设置 serial（或 host）。',
        'backup.err.multi_device':
            '检测到多台 ADB 设备（{devices}），无法自动选择。'
            '请在配置中设置 serial，或在交互终端中选择。',
        'backup.err.with_hint': '{hint}：{message}',
        'backup.info.multi_device': '检测到多台 ADB 设备：',
        'backup.hint.host_multi': '已连接 {host}，但匹配到多台设备',
        'backup.prompt.device_index': '请输入要备份的设备序号：',
        'backup.err.no_device_chosen': '未选择设备，已退出。',
        'backup.err.bad_index': '无效的序号：{answer}',
        'backup.err.index_range': '序号超出范围：{choice}',
        'backup.err.adb_unavailable':
            'adb 不可用，请检查调试授权和 ADB 路径（serial {serial}）',
        'backup.err.source_dir_empty': 'SOURCE_DIR 不能为空',
        'backup.err.unknown_compressor':
            '未知压缩类型：{kind}（可选 xz / gzip / zstd / none）',
        'backup.err.unknown_source_mode':
            '未知 SOURCE_MODE：{mode}（可选 host-adb / device-python）',
        'backup.err.invalid_loglevel':
            '无效日志级别：{level}（可选 quiet/error/warn/info/debug/trace）',
        'backup.err.invalid_progress_interval':
            'PROGRESS_INTERVAL 必须是不小于 0.1 的秒数',
        'backup.err.transfer_failed':
            '传输失败：Android 源退出码 {source}，压缩器退出码 {compressor}{detail}',
        'backup.err.transfer_failed_device':
            '传输失败：设备 Python 源退出码 {source}，压缩器退出码 {compressor}{detail}',
        'backup.err.verify_failed': '归档校验未通过：{detail}',
        'backup.err.no_entries': '未列出任何条目：{source}',
        'backup.err.fatal': '{err}',
        'backup.err.config_read': '无法读取配置文件 {path}: {err}',
        'backup.err.config_line': '配置文件第 {number} 行不是顶层 key: value',
        'backup.err.config_string': '配置文件第 {number} 行字符串无效',
        'backup.err.config_must_be_string': '配置文件第 {number} 行必须是字符串',
        'backup.err.config_no_lists': '配置文件第 {number} 行不支持列表/映射值',
        'backup.err.config_missing': '指定的配置文件不存在：{path}',
        'backup.err.adb_launch': '无法启动 adb {adb}: {err}',
        'backup.err.adb_command_failed': 'adb 命令失败',
        'backup.err.no_remote_rc':
            'adb exec-out {command} 未返回有效的远端退出码',
        'backup.out.confirm':
            'OUT “{out}” 与 compress={compress} 的理论后缀“{suffix}”不符。'
            '自动追加后缀写为“{path}{suffix}”？[Y/n] ',
        'backup.err.out_is_dir':
            '输出路径已存在且是目录，无法写入归档文件：{path}',
        'backup.err.out_not_regular':
            '输出路径已存在且不是普通文件（管道/设备/其他特殊文件）：{path}',
        'backup.err.out_locked':
            '输出文件无法替换（只读或被其他程序占用）：{path}（{err}）',
        'backup.err.out_parent':
            '无法使用输出目录 {path}：{err}',
        'backup.out.retry_prompt':
            '输入新的输出路径（直接回车放弃本次备份）：',
        'backup.out.overwrite_prompt':
            '输出文件已存在：{path}。覆盖它吗？[y/N] ',
        'backup.out.overwrite_declined':
            '已按你的选择保留原有文件：{path}',
        'backup.err.out_exists':
            '输出文件已存在，为避免误覆盖已中止：{path}（如需直接覆写请加 --force / -f）',
        'backup.err.publish_failed':
            '归档已校验通过，但无法写入目标 {path}：{err}。'
            '校验过的归档保留在 {partial}，可手动移动或改名后使用。',
        'backup.step.check': '检查 ADB 与源目录...',
        'backup.step.stream': '通过 PAX tar 与压缩器流式传输 Android 源...',
        'backup.step.verify': '校验归档...',
        'backup.done.archive': '{path}',
        'backup.info.size': '大小: {size} 字节',
        'backup.progress.device_sending': '设备端打包中，已接收 {size}{rate}',
        'backup.progress.rate': '，速率 {rate}/s',
        'backup.progress.device_start': '设备端 Python 开始打包...',
        'backup.progress.device_done': '设备端打包完成，共接收 {size}',
        'backup.warn.device_selfcheck': '设备端 Python 自检失败，重新上传一次...',
        'backup.err.device_python_missing': '设备 Python 不存在：{path}',
        'backup.err.device_python_unusable':
            '设备端 Python 无法执行（上传或兼容性问题）。已删除设备缓存环境，'
            '请检查 DEVICE_PYTHON 与设备 ABI，或重新下载解释器后重试。',
        'backup.err.prefix_no_bin':
            'DEVICE_PYTHON 目录不是有效的 Python prefix（缺少 bin/）：{prefix}',
        'backup.err.prefix_bin_unreadable': '无法读取 DEVICE_PYTHON 的 bin/：{err}',
        'backup.err.prefix_no_interpreter':
            'DEVICE_PYTHON 目录的 bin/ 下未找到 python 解释器：{prefix}',
        'backup.err.mkdir_cache': '无法创建设备缓存目录',
        'backup.err.push_prefix': '上传设备 Python 环境失败',
        'backup.err.unpack_prefix': '解压设备 Python 环境失败',
        'backup.err.push_python': '上传设备 Python 失败',
        'backup.err.push_script': '上传设备脚本 {name} 失败',
        'backup.err.chmod_python': '设置设备 Python 执行权限失败',
        'backup.err.write_stamp': '写入设备缓存标识失败',
        'backup.err.clean_device_env': '清理设备端 Python 环境失败：{err}',
        'backup.err.mkdir_run': '无法创建设备运行目录',
        'backup.info.reuse_env': '复用 Android 端 Python 环境：{dir}',
        'backup.info.uploaded_env': '已上传 Android 端 Python 环境：{dir}',
        'backup.info.kept_env': '已保留 Android 端 Python 环境：{dir}',
        'backup.info.removed_env': '已删除 Android 端 Python 环境',
        'backup.prompt.keep_env': '保留 Android 端 Python 环境以便下次直接复用？[Y/n] ',
        'backup.info.cleaned_host_cache': '已删除主机下载缓存：{dir}',
        'backup.info.cleaned_device_env': '已删除设备端 Python 环境：{dir}',
        'backup.tree.find_rc':
            '# find 退出码 {rc}：部分子目录不可读，列表可能不完整（对应条目显示为 [?]）',
        'backup.tree.unreadable_count': '# {count} 个条目无法 stat，只显示名称',
        'backup.tree.no_owner': '# 设备 stat 不支持 %u/%g，属主/组显示为 -',
        'backup.tree.unreadable_entry':
            '[?] ??? {name}（无法 stat，可能权限不足）',
        'backup.tree.progress.enumerate':
            '正在枚举设备目录...（已接收 {size}，{done} 个条目）',
        'backup.tree.progress.receive':
            '正在接收设备端一次性枚举：{done}/{total} 个条目（{size}）',
        'backup.tree.progress.stat':
            '已统计 {done}/{total} 个条目（每条一次 ADB 往返，大目录较慢）',
        'backup.tree.progress.links':
            '已读取 {done}/{total} 个符号链接',
        'backup.tree.progress.device':
            '设备端 Python 正在列举：已完成 {done} 个条目',
        'backup.tree.err.device_python':
            '设备端 Python 列举失败：{detail}',
        'backup.tree.err.oneshot_unusable':
            '设备端一次性枚举不可用（--tree-mode oneshot），未回退；'
            '请去掉该选项以允许回退',
        'backup.tree.warn.dropped':
            '{count} 行枚举结果无法解析（例如路径含换行），对应条目显示为 [?]',
        'backup.tree.warn.fallback':
            '设备端一次性枚举不可用（{err}），已退回逐条 stat',
        'backup.tree.warn.mismatch':
            '设备端一次性枚举与目录枚举不一致（{count} != {expected}），已退回逐条 stat',
        'backup.done.tree': '已写出目录树：{path}（{count} 个条目）',
        'backup.cli.description':
            'AndBackup 主控：按功能执行流式归档与维护（backup / tree / clean）',
        'backup.cli.config_help':
            '配置文件路径（默认脚本目录中的 backup-android.yaml；'
            '覆盖 BACKUP_CONFIG_FILE）',
        'backup.cli.log_level_help': '日志级别，覆盖配置中的 log_level',
        'backup.err.tree_mode':
            '未知的 tree 枚举方式：{mode}（可选 auto / oneshot / per-entry）',
        'backup.cli.backup_help': '备份设备目录到主机归档（默认功能）',
        'backup.cli.tree_help': '只列出源目录的详细信息树（不备份）',
        'backup.cli.clean_help': '清理缓存：设备端 Python 环境或主机端下载缓存',
        'backup.cli.clean_target_help':
            '要清理的缓存：env=设备端 Python 环境，host-cache=主机端下载/解压'
            '缓存，all=两者（缺省 all）',
        'backup.cli.progress_help':
            '进度输出最小间隔秒数，覆盖配置中的 progress_interval',
        'backup.cli.show_rate_help': '显示 ADB 有效载荷速率，覆盖配置中的 show_rate',
        'backup.cli.clean_env_help':
            '本次运行成功后，顺带删除设备端缓存的 Android Python 环境',
        'backup.cli.clean_host_cache_help':
            '本次运行成功后，顺带删除主机端下载/解压缓存',
        'backup.cli.tree_out_help': '把目录树写入文件（默认写 stdout）',
        'backup.cli.tree_mode_help':
            'tree 的枚举方式：auto（默认，设备端 Python 已部署时优先用它，'
            '否则设备端一次性 find）/ device-python（用设备端 Python 一次性'
            '自汇总，必要时上传解释器）/ oneshot（设备端一次性 find）/ '
            'per-entry（逐条 stat，最慢但最兼容）',
        'backup.cli.force_help':
            '目标文件已存在时直接覆写，不再询问（非交互环境本来就会报错退出）',
        'backup.cli.legacy_flag':
            '`{old}` 已改为子命令：请用 `{new}`',
        'backup.cli.legacy_clean':
            '`{old}` 现在只表示“本次运行成功后顺带清理”：单独清理请用 '
            '`{new}`，或写 `backup.py backup {old}`',
        # ---- prune.py / packed manifest --------------------------
        'prune.info.planned':
            '将删除 {count} 个已打包条目，保留 {kept} 个未打包/不完整条目',
        'prune.prompt.confirm': '确认删除这 {count} 个已打包的源条目？[y/N] ',
        'prune.info.deleted':
            '已删除 {count} 个已打包条目（其中目录 {dirs} 个），保留 {kept} 个未打包或未能删除的条目',
        'prune.info.dry_run': '演练模式：本应删除 {count} 个条目（未执行）',
        'prune.info.nothing': '没有可删除的已打包条目',
        'prune.warn.no_manifest': '未取得已打包清单，跳过删除源条目',
        'prune.warn.incomplete_listing':
            '源目录枚举不完整，仅删除已打包的普通文件与符号链接（保留目录）',
        'prune.warn.kept_dir': '目录 {path} 下存在未打包条目，保留该目录',
        'prune.warn.delete_failed': '删除 {path} 失败：{err}',
        'prune.err.dry_run_needs_source':
            '--prune-dry-run 需要与 --prune-source 一起使用',
        'prune.cli.source_help':
            '归档验证并发布后，删除已成功打包的源条目（仅命令行选项，不读配置）',
        'prune.cli.dry_run_help': '配合 --prune-source：只列出将删除的条目，不真的删除',
        'prune.cli.manifest_help':
            '把本次成功打包/跳过的条目写成 NUL 分隔清单（供 --prune-source 使用）',
    },
    'en': {
        # ---- OS errors (i18n.os_error) ------------------------------------
        'error.errno.exists': 'the file or directory already exists',
        'error.errno.denied': 'permission denied or held open by another program',
        'error.errno.not_found': 'no such file or directory',
        'error.errno.not_a_dir': 'a path component is not a directory',
        'error.errno.is_a_dir': 'the target is a directory',
        'error.errno.no_space': 'not enough space left on the device or disk',
        'error.errno.read_only_fs': 'the file system is read-only',
        'error.errno.busy': 'the file is in use',
        'error.errno.name_too_long': 'the path is too long',
        # ---- shared command line ------------------------------------------
        'i18n.cli.lang_help':
            'message language: zh / en / auto (default: auto-detect, falls back to en)',
        # ---- paxck.py: stream helpers -------------------------------------
        'paxck.err.zstd_stream_missing':
            'the input is a zstd stream, but this Python has no stdlib zstd '
            'and no zstd is on PATH.',
        'paxck.hint.install_zstd': 'Option 1: install zstd on this host',
        'paxck.hint.upgrade_python': 'Option 2: upgrade to Python 3.14+',
        'paxck.hint.use_xz': 'Option 3: use xz instead (natively supported here)',
        'paxck.err.zstd_pump': 'failed to read the zstd input stream: {err}',
        'paxck.err.zstd_decompress': 'zstd decompression failed: {detail}',
        # ---- paxck.py: writing one regular file ---------------------------
        'paxck.err.read_failed': 'cannot read {label}: {err}',
        'paxck.warn.skip_read': 'skipped {label}: {err}',
        'paxck.warn.size_changed':
            '{label}: size changed between reads ({expected} -> {actual}); '
            'the file is being modified',
        'paxck.err.abort_pack': '{message}; aborting the archive',
        'paxck.err.reopen_failed': 'cannot reopen {label}: {err}',
        'paxck.err.write_source_failed':
            'the data source failed while writing {label}: {err}',
        'paxck.err.stream_unrecoverable':
            'the archive stream is unrecoverable; aborting the archive',
        'paxck.err.short_read':
            'the second read of {label} returned {count} bytes less;',
        'paxck.err.source_changed_verify':
            'the file changed while packing, so verification would fail; '
            'aborting the archive',
        'paxck.err.extra_bytes':
            'the data source returned {count} extra bytes for {label};',
        'paxck.err.source_changed':
            'the file changed while packing; aborting the archive',
        # ---- paxck.py: create / compress ---------------------------------
        'paxck.err.source_missing': 'source directory does not exist -> {root}',
        'paxck.warn.walk_failed': 'cannot traverse {name}: {err}',
        'paxck.err.unknown_compressor':
            'unknown compressor {kind} (choose xz / gzip / zstd / none)',
        'paxck.err.zstd_unsupported':
            'no usable zstd support here (no stdlib zstd, no zstd on PATH)',
        # ---- paxck.py: verify --------------------------------------------
        'paxck.verify.unreadable': 'cannot read {path}: {err}',
        'paxck.verify.unparsable': 'cannot parse the archive: {err}',
        'paxck.verify.truncated_hint':
            '(for a compressed stream this usually means a truncated transfer)',
        'paxck.verify.entry_unreadable': '{name}: cannot be read',
        'paxck.verify.mismatch':
            '{name}: SHA-256 mismatch (recorded {expected}…, actual {actual}…)',
        'paxck.verify.stream_broken': 'the stream broke after entry {count}: {err}',
        'paxck.verify.empty':
            'the archive is empty (0 entries) — the tar step most likely failed',
        'paxck.verify.no_checksum_records':
            'all {count} regular files lack the {key} record, so no content was '
            'verified.',
        'paxck.verify.no_checksum_origin':
            'this archive was not produced by paxck create (or its PAX extended '
            'headers were stripped).',
        'paxck.verify.no_regular_files':
            'note: the archive has no regular files, so no content was verified',
        'paxck.verify.more_failures': '... {count} more entries omitted',
        'paxck.verify.incomplete_hint':
            'hint: the archive is incomplete (interrupted transfer?); send it again',
        'paxck.verify.summary':
            '\n{total} entries: SHA-256 ok {ok}, failed {bad}, no record {skip}',
        # ---- paxck.py: extraction safety ---------------------------------
        'paxck.extract.empty_path': 'empty member path',
        'paxck.extract.nul_path': 'member path contains NUL: {name}',
        'paxck.extract.unsafe_path':
            'member path is not a safe relative POSIX path: {name}',
        'paxck.extract.dot_path':
            'member path has an empty, "." or ".." component: {name}',
        'paxck.extract.drive_path':
            'member path contains Windows drive syntax: {name}',
        'paxck.extract.escape': 'member path escapes the destination directory',
        'paxck.extract.bad_parent':
            'member parent is not a real, already-created directory: {path}',
        'paxck.extract.missing_checksum':
            '{name}: regular file has no {key}; refusing to extract unverified '
            'content',
        'paxck.extract.no_content':
            '{name}: tarfile could not provide the file content',
        'paxck.extract.length_mismatch':
            '{name}: read {size} bytes but the tar header records {expected}',
        'paxck.extract.mismatch':
            '{name}: SHA-256 mismatch (recorded {expected}…, actual {actual}…)',
        'paxck.err.archive_unreadable': 'cannot read archive {path}: {err}',
        'paxck.extract.duplicate':
            'the archive contains a duplicate entry: {name}',
        'paxck.extract.link_nul': '{name}: symlink target contains NUL',
        'paxck.extract.unsupported_type':
            '{name}: unsupported tar member type {type}',
        'paxck.extract.empty': 'the archive is empty (0 entries)',
        'paxck.extract.bad_hardlink':
            '{name}: hardlink target is not an extracted regular file: {target}',
        'paxck.warn.chmod_failed': 'cannot restore the mode of {name}: {err}',
        'paxck.warn.utime_failed': 'cannot restore the mtime of {name}: {err}',
        # ---- paxck.py: extract commands ----------------------------------
        'paxck.extract.exists': 'destination already exists: {path}',
        'paxck.extract.no_parent':
            'the parent directory of the destination does not exist: {path}',
        'paxck.done.extracted': 'verified and extracted to {path}',
        'paxck.extract.refused': 'refusing to extract this archive: {err}',
        'paxck.extract.input_failed': '{err}',
        'paxck.extract.damaged':
            'the archive is corrupt or truncated; nothing was extracted: {err}',
        'paxck.extract.write_failed':
            'extraction failed; the destination was not published: {err}',
        'paxck.direct.not_a_dir':
            'the direct-tarfile destination is not a directory: {path}',
        'paxck.direct.mkdir_failed':
            'cannot create the extraction destination: {err}',
        'paxck.direct.damaged':
            'direct tarfile extraction failed: the archive is corrupt or '
            'truncated: {err}',
        'paxck.direct.failed': 'direct tarfile extraction failed: {err}',
        'paxck.done.direct_extracted':
            'extracted directly with tarfile to {path} (PAX SHA-256 not '
            'verified, non-atomic)',
        # ---- paxck.py: command line --------------------------------------
        'paxck.cli.description':
            'create/verify tar archives with an embedded PAX SHA-256 '
            '(streaming, standard library only)',
        'paxck.cli.create_help': 'pack a local directory to stdout',
        'paxck.cli.compress_help':
            'compress stdin to stdout (replaces external xz/gzip)',
        'paxck.cli.verify_help': 'verify an archive (auto-detects xz/gzip)',
        'paxck.cli.extract_help':
            'extract an archive (verified and atomic by default)',
        'paxck.cli.path_help': 'archive path; stdin is read when omitted',
        'paxck.cli.input_help': 'same as the positional argument: archive path',
        'paxck.cli.directory_help':
            'default mode: a destination that must not exist yet; direct mode: '
            'an existing directory is allowed',
        'paxck.cli.direct_help':
            'call tarfile directly; skips verification and atomicity, only for '
            'trusted archives',
        # ---- android_python.py ----------------------------------
        'py.err.download': 'cannot download the Android Python from {url}: {err}',
        'py.err.write_download': 'cannot write the downloaded file {path}: {err}',
        'py.err.no_zstd': 'no usable zstd decompressor',
        'py.err.zstd_failed': 'zstd decompression failed',
        'py.err.zstd_failed_detail': 'zstd decompression failed: {detail}',
        'py.err.no_zstd_or_tar':
            'zstd support is required to unpack the interpreter: this Python '
            'has no compression.zstd, no zstd is on PATH, and no tar is usable',
        'py.err.no_zstd_or_bsdtar': 'no usable zstd decompressor or tar',
        'py.err.tar_read_failed': 'tar cannot read the archive: {detail}',
        'py.err.tar_extract_failed': 'tar failed to unpack the interpreter',
        'py.err.tar_extract_failed_detail':
            'tar failed to unpack the interpreter: {detail}',
        'py.err.interpreter_missing': 'no bin/python3* interpreter found in the archive',
        'py.err.entry_unreadable': 'cannot read the archive member: {name}',
        'py.err.invalid_prefix':
            'the extracted result has no bin/python*, so it is not a valid '
            'Python prefix',
        'py.err.no_prefix':
            'the download/unpack produced no valid Python prefix: {prefix}',
        'py.err.device_python_missing': 'the Android Python does not exist: {path}',
        'py.hint.point_or_download':
            '  point device_python at an existing interpreter, or set '
            'download_device_python: true to download it there',
        'py.err.mode_needs_python':
            'source_mode=device-python needs an Android-side Python:',
        'py.hint.set_device_python':
            '  · set device_python to an existing single-file interpreter or a '
            'prefix directory, or',
        'py.hint.enable_download':
            '  · set download_device_python: true so the pinned '
            'python-build-standalone build is downloaded into the cache',
        'py.log.fetching': 'fetching the Android Python: {url}',
        'py.log.cached_archive': 'using the cached archive: {path}',
        'py.log.unpacking_to': 'unpacking to: {path}',
        'py.log.done': 'done ({method}): {path}',
        # ---- adb_source.py ---------------------------------------
        'adb.err.launch': 'cannot start adb {adb}: {err}',
        'adb.err.exec_out': 'adb exec-out {command}: {detail}',
        'adb.err.remote_rc': 'adb exec-out {command} returned remote exit code {rc}',
        'adb.err.no_status':
            'adb exec-out {command} did not return a valid remote exit code',
        'adb.err.find_not_nul':
            'the adb exec-out find output is not NUL-terminated; refusing to '
            'parse an incomplete listing',
        'adb.err.no_paths': 'adb exec-out listed no entries under {root}',
        'adb.err.stat_unparsable':
            'cannot parse the Android stat output {raw}: {err}',
        'adb.err.invalid_loglevel':
            'invalid log level: {level} (choose quiet/error/warn/info/debug/trace)',
        'adb.progress.discovered': 'discovered {count} entries',
        'adb.progress.enumerating': 'enumerating the Android directory...',
        'adb.progress.enum_label': 'listing',
        'adb.progress.enum_running':
            'still enumerating: {count} entries, {size} of listing data',
        'adb.progress.enum_done':
            'listing complete: {count} entries, {size} of listing data',
        'adb.progress.entry':
            'entry {done}/{total}, ADB payload {size}: {name}{suffix}',
        'adb.progress.entry_skipped': ' (skipped)',
        'adb.debug.entry': 'processing: {name}',
        'adb.progress.transfer': 'transferring, ADB payload {size}{rate}{current}',
        'adb.progress.rate': ', rate {rate}/s',
        'adb.progress.current': ': {name}',
        'adb.progress.finish':
            'done: {done}/{total} entries, ADB payload {size} '
            '(listing {list}, file content {files})',
        'adb.warn.find_rc':
            'find returned exit code {rc} while listing; unreadable entries '
            'will be skipped',
        'adb.warn.skip_metadata':
            'skipped {path}: cannot read metadata: {err}',
        'adb.warn.skip_symlink': 'skipped the symlink {name}: {err}',
        'adb.warn.skip_non_regular': 'skipped the non-regular file {name}',
        'adb.warn.incomplete':
            'some entries could not be read and were skipped; the archive is '
            'still verified and published',
        'adb.cli.description':
            'write an Android directory as a raw PAX tar on stdout via adb exec-out',
        'adb.cli.adb_help': 'path to the adb executable (default: adb)',
        'adb.cli.log_level_help': 'log level',
        'adb.cli.progress_help': 'minimum seconds between progress lines',
        'adb.cli.show_rate_help': 'show the ADB payload rate on progress lines',
        'adb.cli.directory_help': 'absolute, non-root directory on the device',
        'adb.cli.pack_help':
            'list an Android directory and write a raw PAX tar to stdout '
            '(the data-source stage of a pipeline)',
        'adb.err.source_root_absolute':
            'the Android source path must be an absolute, non-root directory: {root}',
        'adb.err.source_not_directory':
            'the Android source path is not a directory: {root}',
        'adb.err.source_list_failed':
            'cannot list the Android source directory: {err}',
        'adb.err.source_root_absolute':
            'the Android source path must be an absolute, non-root directory: {root}',
        'adb.err.source_not_directory':
            'the Android source path is not a directory: {root}',
        'adb.err.source_list_failed':
            'cannot list the Android source directory: {err}',
        'i18n.detail_sep': ': {detail}',
        # ---- backup.py -------------------------------------------
        'backup.err.list_devices': 'cannot list ADB devices',
        'backup.err.connect_failed': 'cannot connect to the ADB device {host}',
        'backup.err.host_no_match':
            'connected to {host}, but no matching device appears in `adb devices`. '
            'Check that the wireless debugging port did not change, or set '
            'serial in the configuration.',
        'backup.err.no_devices':
            'adb found no authorized device. Enable USB debugging and authorize '
            'this host, or set serial (or host) in the configuration.',
        'backup.err.multi_device':
            'several ADB devices found ({devices}); cannot choose automatically. '
            'Set serial in the configuration, or run from an interactive terminal.',
        'backup.err.with_hint': '{hint}: {message}',
        'backup.info.multi_device': 'several ADB devices found:',
        'backup.hint.host_multi': 'connected to {host}, but several devices match',
        'backup.prompt.device_index': 'device number to back up: ',
        'backup.err.no_device_chosen': 'no device selected; exiting.',
        'backup.err.bad_index': 'invalid number: {answer}',
        'backup.err.index_range': 'number out of range: {choice}',
        'backup.err.adb_unavailable':
            'adb is unusable; check debugging authorization and the adb path '
            '(serial {serial})',
        'backup.err.source_dir_empty': 'SOURCE_DIR must not be empty',
        'backup.err.unknown_compressor':
            'unknown compressor: {kind} (choose xz / gzip / zstd / none)',
        'backup.err.unknown_source_mode':
            'unknown source_mode: {mode} (choose host-adb / device-python)',
        'backup.err.invalid_loglevel':
            'invalid log level: {level} (choose quiet/error/warn/info/debug/trace)',
        'backup.err.invalid_progress_interval':
            'PROGRESS_INTERVAL must be a number of seconds no smaller than 0.1',
        'backup.err.transfer_failed':
            'transfer failed: Android source exit code {source}, compressor '
            'exit code {compressor}{detail}',
        'backup.err.transfer_failed_device':
            'transfer failed: device Python source exit code {source}, compressor '
            'exit code {compressor}{detail}',
        'backup.err.verify_failed': 'archive verification failed: {detail}',
        'backup.err.no_entries': 'no entries were listed: {source}',
        'backup.err.fatal': '{err}',
        'backup.err.config_read': 'cannot read the config file {path}: {err}',
        'backup.err.config_line':
            'line {number} of the config file is not a top-level key: value',
        'backup.err.config_string':
            'line {number} of the config file has an invalid string',
        'backup.err.config_must_be_string':
            'line {number} of the config file must be a string',
        'backup.err.config_no_lists':
            'line {number} of the config file may not use list/mapping values',
        'backup.err.config_missing': 'the given config file does not exist: {path}',
        'backup.err.adb_launch': 'cannot start adb {adb}: {err}',
        'backup.err.adb_command_failed': 'an adb command failed',
        'backup.err.no_remote_rc':
            'adb exec-out {command} did not return a valid remote exit code',
        'backup.out.confirm':
            'OUT "{out}" does not match the theoretical suffix "{suffix}" for '
            'compress={compress}. Append the suffix and write '
            '"{path}{suffix}"? [Y/n] ',
        'backup.err.out_is_dir':
            'the output path already exists and is a directory, so the archive '
            'cannot be written there: {path}',
        'backup.err.out_not_regular':
            'the output path already exists and is not a regular file '
            '(pipe/device/other special file): {path}',
        'backup.err.out_locked':
            'the output file cannot be replaced (read-only or held open by '
            'another program): {path} ({err})',
        'backup.err.out_parent':
            'the output directory {path} cannot be used: {err}',
        'backup.out.retry_prompt':
            'enter a new output path (press Enter to give up this backup): ',
        'backup.out.overwrite_prompt':
            'the output file already exists: {path}. Overwrite it? [y/N] ',
        'backup.out.overwrite_declined':
            'kept the existing file as you chose: {path}',
        'backup.err.out_exists':
            'the output file already exists, so the run stopped instead of '
            'overwriting it: {path} (use --force / -f to overwrite)',
        'backup.err.publish_failed':
            'the archive verified, but {path} could not be written: {err}. The '
            'verified archive was kept at {partial}; move or rename it manually.',
        'backup.step.check': 'checking ADB and the source directory...',
        'backup.step.stream':
            'streaming the Android source through PAX tar and the compressor...',
        'backup.step.verify': 'verifying the archive...',
        'backup.done.archive': '{path}',
        'backup.info.size': 'size: {size} bytes',
        'backup.progress.device_sending': 'packing on the device, received {size}{rate}',
        'backup.progress.rate': ', rate {rate}/s',
        'backup.progress.device_start': 'the device Python is starting to pack...',
        'backup.progress.device_done': 'device packing finished, received {size}',
        'backup.warn.device_selfcheck':
            'the device Python failed its self-check; uploading it once more...',
        'backup.err.device_python_missing': 'the Android Python does not exist: {path}',
        'backup.err.device_python_unusable':
            'the device Python cannot run (upload or compatibility problem). The '
            'device cache was removed; check DEVICE_PYTHON and the device ABI, or '
            'download the interpreter again and retry.',
        'backup.err.prefix_no_bin':
            'the DEVICE_PYTHON directory is not a valid Python prefix (no bin/): '
            '{prefix}',
        'backup.err.prefix_bin_unreadable':
            'cannot read the bin/ directory of DEVICE_PYTHON: {err}',
        'backup.err.prefix_no_interpreter':
            'no python interpreter found under bin/ of DEVICE_PYTHON: {prefix}',
        'backup.err.mkdir_cache': 'cannot create the device cache directory',
        'backup.err.push_prefix': 'failed to push the device Python environment',
        'backup.err.unpack_prefix': 'failed to unpack the device Python environment',
        'backup.err.push_python': 'failed to push the device Python',
        'backup.err.push_script': 'failed to push the device script {name}',
        'backup.err.chmod_python': 'failed to make the device Python executable',
        'backup.err.write_stamp': 'failed to write the device cache stamp',
        'backup.err.clean_device_env':
            'failed to clean the device Python environment: {err}',
        'backup.err.mkdir_run': 'cannot create the device run directory',
        'backup.info.reuse_env': 'reusing the Android Python environment: {dir}',
        'backup.info.uploaded_env': 'uploaded the Android Python environment: {dir}',
        'backup.info.kept_env': 'kept the Android Python environment: {dir}',
        'backup.info.removed_env': 'removed the Android Python environment',
        'backup.prompt.keep_env':
            'keep the Android Python environment for reuse next time? [Y/n] ',
        'backup.info.cleaned_host_cache': 'removed the host download cache: {dir}',
        'backup.info.cleaned_device_env':
            'removed the device Python environment: {dir}',
        'backup.tree.find_rc':
            '# find exit code {rc}: some subdirectories are unreadable, so the '
            'list may be incomplete (such entries show as [?])',
        'backup.tree.unreadable_count':
            "# {count} entries cannot be stat'ed; only their names are shown",
        'backup.tree.no_owner':
            '# this device stat has no %u/%g; owner/group are shown as -',
        'backup.tree.unreadable_entry':
            '[?] ??? {name} (cannot stat; probably a permission problem)',
        'backup.tree.progress.enumerate':
            'enumerating the device directory... ({size} received, {done} entries)',
        'backup.tree.progress.receive':
            'receiving the device-side listing: {done}/{total} entries ({size})',
        'backup.tree.progress.stat':
            'collected {done}/{total} entries (one ADB round trip each; large '
            'directories are slow)',
        'backup.tree.progress.links':
            'read {done}/{total} symlink targets',
        'backup.tree.progress.device':
            'the device Python is listing: {done} entries done',
        'backup.tree.err.device_python':
            'the device-side Python listing failed: {detail}',
        'backup.tree.err.oneshot_unusable':
            'the one-shot device listing is unavailable (--tree-mode oneshot) '
            'and no fallback was allowed; drop the option to allow one',
        'backup.tree.warn.dropped':
            '{count} listing records could not be parsed (a newline in a path, '
            'for instance); those entries show as [?]',
        'backup.tree.warn.fallback':
            'the one-shot device listing is unavailable ({err}); falling back to '
            'one stat per entry',
        'backup.tree.warn.mismatch':
            'the one-shot device listing disagrees with the directory listing '
            '({count} != {expected}); falling back to one stat per entry',
        'backup.done.tree': 'wrote the directory tree to {path} ({count} entries)',
        'backup.cli.description':
            'AndBackup controller: streaming archives and maintenance, one '
            'function at a time (backup / tree / clean)',
        'backup.cli.config_help':
            'config file path (defaults to backup-android.yaml next to the script; '
            'overrides BACKUP_CONFIG_FILE)',
        'backup.cli.log_level_help':
            'log level, overriding log_level in the config',
        'backup.err.tree_mode':
            'unknown tree enumeration mode: {mode} (use auto / oneshot / '
            'per-entry)',
        'backup.cli.backup_help': 'back up a device directory to a host archive (default)',
        'backup.cli.tree_help': 'list the detailed source tree only (no backup)',
        'backup.cli.clean_help':
            'clean caches: the device Python environment or the host download cache',
        'backup.cli.clean_target_help':
            'cache to clean: env = device Python environment, host-cache = host '
            'download/unpack cache, all = both (default: all)',
        'backup.cli.progress_help':
            'minimum seconds between progress lines, overriding progress_interval',
        'backup.cli.show_rate_help':
            'show the ADB payload rate, overriding show_rate',
        'backup.cli.clean_env_help':
            'after this run succeeds, also delete the cached device Python environment',
        'backup.cli.clean_host_cache_help':
            'after this run succeeds, also delete the host download/unpack cache',
        'backup.cli.tree_out_help': 'write the tree to a file (default: stdout)',
        'backup.cli.tree_mode_help':
            'how tree enumerates: auto (default: prefer the device Python when '
            'it is already deployed, else the one-shot device find) / '
            'device-python (one self-summarised pass on the device, uploading '
            'an interpreter if needed) / oneshot (device-side find) / '
            'per-entry (slowest, most compatible)',
        'backup.cli.force_help':
            'overwrite an existing target without asking (non-interactive runs '
            'fail instead)',
        'backup.cli.legacy_flag':
            '`{old}` is now a subcommand: use `{new}`',
        'backup.cli.legacy_clean':
            '`{old}` now only means "clean after this run succeeds": clean on '
            'its own with `{new}`, or write `backup.py backup {old}`',
        # ---- prune.py / packed manifest --------------------------
        'prune.info.planned':
            'will delete {count} packed entries and keep {kept} unpacked or '
            'incomplete entries',
        'prune.prompt.confirm': 'delete these {count} packed source entries? [y/N] ',
        'prune.info.deleted':
            'deleted {count} packed entries ({dirs} of them directories); kept '
            '{kept} unpacked or undeletable entries',
        'prune.info.dry_run': 'dry run: {count} entries would be deleted (nothing done)',
        'prune.info.nothing': 'no packed entry can be deleted',
        'prune.warn.no_manifest':
            'no packed manifest was produced; skipping source deletion',
        'prune.warn.incomplete_listing':
            'the source listing was incomplete, so only packed files and '
            'symlinks were deleted (directories kept)',
        'prune.warn.kept_dir':
            'kept the directory {path}: it still contains unpacked entries',
        'prune.warn.delete_failed': 'failed to delete {path}: {err}',
        'prune.err.dry_run_needs_source':
            '--prune-dry-run must be used together with --prune-source',
        'prune.cli.source_help':
            'after the archive is verified and published, delete the source '
            'entries that were packed (command-line only; the config file is '
            'not consulted)',
        'prune.cli.dry_run_help':
            'with --prune-source: list what would be deleted without deleting',
        'prune.cli.manifest_help':
            'write the packed/skipped entries as a NUL-separated manifest (used '
            'by --prune-source)',
        # ---- end of catalog ----
    },
}


_state = {'lang': None}


def tag(tag_name):
    """Return a language-neutral status tag such as ``[ERROR]``."""
    try:
        return TAGS[tag_name]
    except KeyError:
        raise ValueError(f'unknown tag {tag_name!r}') from None


def normalize(value):
    """Map a locale-ish string onto a supported language, else None."""
    if not value:
        return None
    text = str(value).strip().lower().replace('-', '_')
    if not text or text == 'auto':
        return None
    # Windows reports names such as 'Chinese (Simplified)_China'.
    if 'chinese' in text:
        return 'zh'
    if 'english' in text:
        return 'en'
    head = text.split('.')[0].split('@')[0].split('_')[0].split(' ')[0]
    if head in ('zh', 'cn', 'chs', 'cht', 'zhongwen'):
        return 'zh'
    if head in ('en', 'eng', 'c', 'posix'):
        return 'en'
    return None


def _os_language_name():
    """Return the OS language tag (e.g. ``zh_CN``) that is worth trusting.

    On Windows, ``locale.getlocale()`` describes the *process* C locale, which
    Python's UTF-8 mode forces to English even on a Chinese system, while the
    system message tables — and therefore ``OSError.strerror`` — follow the
    user interface language.  Asking Win32 keeps both halves of a message in
    the same language.  Other platforms have no such split, so they use the
    locale modules instead (see :func:`_detect`).
    """
    if os.name != 'nt':
        return None
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
    except (ImportError, AttributeError, OSError):
        return None
    name = locale.windows_locale.get(kernel32.GetUserDefaultUILanguage())
    if name:
        return name
    buffer = ctypes.create_unicode_buffer(85)  # LOCALE_NAME_MAX_LENGTH
    if kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
        return buffer.value
    return None


def _detect(env=None):
    """Guess the language from locale environment variables, then the OS."""
    env = os.environ if env is None else env
    for name in ('LC_ALL', 'LC_MESSAGES', 'LANGUAGE', 'LANG'):
        found = normalize(env.get(name))
        if found:
            return found
    found = normalize(_os_language_name())
    if found:
        return found
    for probe in (lambda: locale.getlocale()[0],
                  lambda: locale.getdefaultlocale()[0]):
        try:
            found = normalize(probe())
        except (TypeError, ValueError, AttributeError):
            found = None
        if found:
            return found
    return None


def resolve(cli=None, env=None):
    """Resolve the effective language: CLI, environment, locale, fallback."""
    env = os.environ if env is None else env
    for candidate in (cli, env.get(ENV_VAR)):
        found = normalize(candidate)
        if found:
            return found
    return _detect(env) or FALLBACK


def set_language(lang):
    """Pin the process language; ``None``/``auto``/empty resumes detection."""
    if lang is None or str(lang).strip().lower() in ('', AUTO):
        _state['lang'] = None
        return None
    found = normalize(lang)
    if found is None:
        raise ValueError(
            f'unsupported language {lang!r} (available: {", ".join(LANGUAGES)})')
    _state['lang'] = found
    return found


def language():
    """Return the language in effect, detecting one on first use."""
    if _state['lang'] is None:
        _state['lang'] = resolve()
    return _state['lang']


def t(message_key, **kwargs):
    """Translate ``message_key``; other languages are tried before giving up.

    The parameter is deliberately not named ``key``: catalog templates use
    ``{key}`` as a placeholder (e.g. the PAX checksum header name).
    """
    order = list(LANGUAGES)
    current = language()
    if current in order:
        order.remove(current)
    template = None
    for lang in [current] + order:
        template = MESSAGES[lang].get(message_key)
        if template is not None:
            break
    if template is None:
        return message_key
    return template.format(**kwargs) if kwargs else template


def export(env=None):
    """Put the resolved language into ``env`` so child tools agree."""
    env = os.environ if env is None else env
    env[ENV_VAR] = language()
    return env


# errno -> catalog key.  Built defensively because not every platform defines
# every errno name.
_ERRNO_KEYS = {
    getattr(errno, name): key for name, key in (
        ('EEXIST', 'error.errno.exists'),
        ('EACCES', 'error.errno.denied'),
        ('EPERM', 'error.errno.denied'),
        ('ENOENT', 'error.errno.not_found'),
        ('ENOTDIR', 'error.errno.not_a_dir'),
        ('EISDIR', 'error.errno.is_a_dir'),
        ('ENOSPC', 'error.errno.no_space'),
        ('EROFS', 'error.errno.read_only_fs'),
        ('EBUSY', 'error.errno.busy'),
        ('ENAMETOOLONG', 'error.errno.name_too_long'),
    ) if getattr(errno, name, None) is not None
}


def os_error(error):
    """Localize an ``OSError`` into one short phrase.

    ``OSError.strerror`` is written by the OS in the OS language, so embedding
    it verbatim would mix languages whenever the user picked another one (a
    Chinese Windows error inside an English sentence).  Known errnos are
    therefore rendered from our own catalog; unknown ones keep the OS text,
    which is the only description available.
    """
    message_key = _ERRNO_KEYS.get(getattr(error, 'errno', None))
    if message_key is not None:
        return t(message_key)
    return getattr(error, 'strerror', None) or str(error)


def can_prompt(log_level='info'):
    """True when a console can really answer a localized prompt.

    Lives here because every prompt is a translated question, and every
    command module already imports this one; ``quiet``/``error`` never ask.
    ``sys.stdin.isatty()`` alone is not enough on Windows: a NUL/DEVNULL stdin
    is reported as a TTY, so an automated run would print questions nobody can
    answer.  A real console handle is required there instead.
    """
    if str(log_level).lower() in ('quiet', 'error'):
        return False
    stdin = sys.stdin
    try:
        if stdin is None or not stdin.isatty():
            return False
    except (AttributeError, ValueError, OSError):
        return False
    if os.name != 'nt':
        return True
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stdin.fileno())
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(
            ctypes.c_void_p(handle), ctypes.byref(mode)))
    except (ImportError, AttributeError, ValueError, OSError):
        # No console probe (or a stdin without an OS handle, such as a test
        # double): trust ``isatty()`` rather than losing interactivity.
        return True


def lang_help():
    """Translated help text for the shared ``--lang`` option."""
    return t('i18n.cli.lang_help')


def prescan_lang(argv=None):
    """Read ``--lang`` from raw argv before argparse is built.

    Help strings must be translated *while* the parser is created, so the
    language has to be known before ``parse_args`` runs.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    for index, item in enumerate(args):
        if item == '--lang':
            return args[index + 1] if index + 1 < len(args) else None
        if item.startswith('--lang='):
            return item.split('=', 1)[1]
    return None
