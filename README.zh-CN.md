# WinBarMonitor

[![CI](https://github.com/RS-zlk/WinBarMonitor/actions/workflows/ci.yml/badge.svg)](https://github.com/RS-zlk/WinBarMonitor/actions/workflows/ci.yml)

WinBarMonitor 是一个零第三方依赖的 [SwiftBar](https://swiftbar.app/)
插件，用 macOS 菜单栏显示远程 Windows 电脑的 CPU、内存、C 盘、网络、运行时间、
Top 进程以及可选的 NVIDIA GPU 指标。远端只执行只读 PowerShell/CIM 查询和可选的
`nvidia-smi` 查询。

[English documentation](README.md)

## 特性

- 默认使用通用 SSH alias：`windows-monitor`。
- 菜单显示顺序为：远端 hostname → 配置的 alias → `Windows PC`。
- 在线、缓存、离线分别使用绿色、黄色、红色彩色 PNG 图标。
- 本地缓存最近一次成功指标，短暂断网时仍可查看。
- 成功采集的数据默认在本地 SQLite 中保留 30 天，并生成浏览器统计页面。
- 机器专属设置保存在 Git 忽略的 `.winbar.env` 中。
- 不需要第三方 Python 包，也不需要本项目提供的服务器。
- 支持无 NVIDIA GPU、多 GPU、`N/A` 值和较慢 SSH 链路。

## 运行要求

- 安装 Python 3 和 [SwiftBar](https://swiftbar.app/) 的 macOS。
- macOS 自带的 `ssh` 客户端及可连接 Windows 的 SSH alias。
- Windows OpenSSH Server、PowerShell，以及账号可用的 CIM/WMI 权限。
- 只有需要 GPU 指标时才需要 NVIDIA 驱动和 `nvidia-smi`。

项目没有第三方 Python 依赖，只使用 Python 标准库、POSIX shell、macOS `ssh` 和
Windows 内置组件。

## 安装

1. 安装并运行 SwiftBar，选择插件目录。
2. 创建机器本地配置：

   ```sh
   cp .winbar.env.example .winbar.env
   ```

   编辑 `.winbar.env`，把 `WINBAR_SSH_ALIAS` 改为 Windows 电脑对应的 SSH
   config alias。该文件已被 Git 忽略。
3. 先确认 SSH 可以无交互连接：

   ```sh
   ssh windows-monitor  # 请替换为 .winbar.env 中配置的 alias。
   ```

4. 安装插件：

   ```sh
   ./install.sh
   ```

刷新 SwiftBar 即可。生成的插件文件名控制刷新频率，安装程序会把本地配置复制到
SwiftBar，并设置为仅当前用户可读写。点击菜单会立即打开，不会强制发起 SSH；需要时
可选择手动刷新。

## Windows OpenSSH 与密钥

在 Windows 安装并启动 OpenSSH Server 可选功能，允许 Windows 防火墙中的 SSH 访问，
并使用权限最小化的专用账号。在 macOS 使用 `ssh-keygen` 创建密钥，将公钥加入
Windows 账号的 authorized keys 文件，然后在 `~/.ssh/config` 添加：

```sshconfig
Host windows-monitor
    HostName windows.example.invalid
    User monitor
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

建议通过内网或 VPN 访问、限制防火墙来源，并保持 SSH `BatchMode` 认证。不要将私钥、
密码、token 或真实主机地址提交到仓库。

## 本地配置

`.winbar.env` 使用 shell 风格的 `KEY=VALUE`。真实主机 alias 和机器路径应只放在
这个文件中，不要写入被追踪的源码。安装程序用它确定插件目录和刷新间隔，并把它复制
到已安装的支持模块旁。运行时环境变量的优先级高于配置文件。

所有配置均可选：

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `WINBAR_SSH_ALIAS` | `windows-monitor` | SSH config alias |
| `WINBAR_REFRESH_SECONDS` | `30` | 正整数刷新间隔 |
| `WINBAR_SSH_TIMEOUT` | `15` | 正整数采集超时 |
| `WINBAR_CACHE_PATH` | `~/.cache/winbar-monitor/cache.json` | 本地缓存文件 |
| `WINBAR_HISTORY_PATH` | `~/.local/share/winbar-monitor/history.sqlite3` | 本地历史数据库 |
| `WINBAR_REPORT_PATH` | `~/.local/share/winbar-monitor/history.html` | 生成的统计页面 |
| `WINBAR_RETENTION_DAYS` | `30` | 原始采样保留天数 |
| `SWIFTBAR_PLUGIN_DIR` | SwiftBar 标准插件目录 | 安装位置 |

运行安装或卸载脚本时，可通过 `WINBAR_CONFIG_FILE=/path/to/settings.env` 使用其他
名称的本地配置。数值配置小于 1 时会按 1 处理。

## 历史统计页面

每次成功采集都会向 SQLite 追加一条记录，并重新生成一个自包含 HTML 页面。
SwiftBar 菜单只提供“查看历史统计”入口，汇总和图表均在浏览器中展示。

页面提供最近 24 小时、7 天和 30 天的 CPU/GPU 利用率、内存/显存、GPU 温度/功耗
以及网络速率。多 GPU 情况使用最高利用率和温度，并统计显存与功耗合计。采集失败不会
写成错误的零值。页面不加载外部 JavaScript、统计脚本、字体或网络资源。
鼠标悬停在折线节点上时，会显示该节点的本地时间和各条曲线数值。

## 安全与隐私

插件通过 SSH 发送一条编码后的只读 PowerShell 命令，不上传指标、不监听入站连接、
也不保存凭据。SSH 密钥、缓存、历史和报告均由用户控制。缓存可能包含 hostname 和
进程名，历史数据会反映机器使用规律；请勿把生成文件附加到公开 issue。部署到受管
设备前请阅读[安全策略](SECURITY.md)。

## 卸载

使用安装时的同样配置运行：

```sh
./uninstall.sh
```

脚本会移除生成的 SwiftBar 文件及复制的配置，但保留源码目录中的 `.winbar.env`、
缓存、历史和报告。如需删除本地监控数据：

```sh
rm -f "$HOME/.cache/winbar-monitor/cache.json"
rm -f "$HOME/.local/share/winbar-monitor/history.sqlite3" \
      "$HOME/.local/share/winbar-monitor/history.sqlite3-shm" \
      "$HOME/.local/share/winbar-monitor/history.sqlite3-wal" \
      "$HOME/.local/share/winbar-monitor/history.html"
```

## 故障排查

- 先运行 `ssh windows-monitor`，解决 SSH 认证或名称解析问题后再检查插件。
- 黄色表示采集失败但正在显示本地缓存；红色表示没有可用缓存。
- GPU 字段为 `N/A` 时，在 Windows SSH 会话中检查 `nvidia-smi`。没有 NVIDIA 硬件
  的电脑仍然受支持，只是不显示 GPU 部分。
- 慢链路可增大 `WINBAR_SSH_TIMEOUT`，并使用 SSH 连接复用。不要把 Windows SSH
  服务暴露到公网。

## 开发与测试

运行标准库测试和 shell 语法检查：

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile winbar_monitor.py winbar.1m.py
sh -n install.sh uninstall.sh
```

测试只使用合成 fixture；CI 不会发起真实 SSH 连接。贡献流程见
[CONTRIBUTING.md](CONTRIBUTING.md)。

仓库不提交真实主机截图或生成的报告；测试只使用合成 fixture，避免暴露机器数据。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
