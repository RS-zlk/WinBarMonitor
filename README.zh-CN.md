# WinBarMonitor

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
2. 创建名为 `windows-monitor` 的 SSH config alias，或设置
   `WINBAR_SSH_ALIAS`。
3. 先确认 SSH 可以无交互连接：

   ```sh
   ssh windows-monitor
   ```

4. 安装插件：

   ```sh
   ./install.sh
   # 可选：设置刷新间隔和插件目录。
   WINBAR_REFRESH_SECONDS=10 SWIFTBAR_PLUGIN_DIR="/path/to/plugins" ./install.sh
   ```

刷新 SwiftBar 即可。生成的插件文件名控制刷新频率，菜单中也提供手动刷新操作。

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

## 配置

所有变量均可选：

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `WINBAR_SSH_ALIAS` | `windows-monitor` | SSH config alias |
| `WINBAR_REFRESH_SECONDS` | `30` | 正整数刷新间隔 |
| `WINBAR_SSH_TIMEOUT` | `15` | 正整数采集超时 |
| `WINBAR_CACHE_PATH` | `~/.cache/winbar-monitor/cache.json` | 本地缓存文件 |

刷新间隔和超时小于 1 秒时会按 1 秒处理。成功采集后缓存以原子方式写入，且不会
上传到服务器。

## 安全与隐私

插件通过 SSH 发送一条编码后的只读 PowerShell 命令，不上传指标、不监听入站连接、
也不保存凭据。SSH 密钥和缓存均由用户控制。部署到受管设备前请阅读
[安全策略](SECURITY.md)。

## 卸载

使用安装时的同样配置运行：

```sh
./uninstall.sh
```

脚本会移除生成的 SwiftBar 文件，但保留本地缓存。如需删除缓存：

```sh
rm -f "$HOME/.cache/winbar-monitor/cache.json"
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
sh -n install.sh uninstall.sh winbar.1m.py
```

测试只使用合成 fixture；CI 不会发起真实 SSH 连接。贡献流程见
[CONTRIBUTING.md](CONTRIBUTING.md)。

首个版本暂不附带截图：制作安全截图需要真实主机，因此文档和测试只使用合成数据。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
