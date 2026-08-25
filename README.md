# WinBarMonitor

WinBarMonitor 是一个零依赖的 SwiftBar 插件：在 macOS 顶部菜单栏显示远程 Windows 11 的 CPU、内存、C 盘、网络、运行时间和 NVIDIA GPU 指标。远程端只执行只读 PowerShell/CIM 查询及 `nvidia-smi`，不会修改 Windows。

## 安装

1. 安装并运行 [SwiftBar](https://swiftbar.app/)，选择插件目录。
2. 在本目录执行 `./install.sh`。若 SwiftBar 使用了自定义目录：`SWIFTBAR_PLUGIN_DIR="/path/to/plugins" ./install.sh`。
3. 确认 Mac 已可无交互执行 `ssh y9000p-remote`（推荐 SSH key，不要把密码或私钥写入仓库）。
4. SwiftBar 选择刷新插件。插件默认每 30 秒刷新，点击顶部图标即可展开实时指标。

环境变量：`WINBAR_SSH_ALIAS`（默认 `y9000p-remote`）、`WINBAR_REFRESH_SECONDS`（默认 30）、`WINBAR_SSH_TIMEOUT`（默认 5）、`WINBAR_CACHE_PATH`（默认 `~/.cache/winbar-monitor/cache.json`）。SwiftBar 插件文件名中的 `1m` 是它的刷新兜底周期，环境变量可将采集窗口设得更短。

## 安全与网络

插件使用 `ssh -o BatchMode=yes -o ConnectTimeout=5`，连接失败会快速返回。最近一次成功结果保存在本机缓存，离线时显示“缓存”状态和错误信息，不会卡住菜单栏。建议通过 Tailscale/SSH 隧道连接，不要把 PowerShell/监控端口暴露到公网；SSH 服务本身请使用密钥、最小权限账号和防火墙白名单。阿里云服务器可作为 Tailscale/反向 SSH 的中继，但本插件不需要把指标上传到服务器。

## 开发与测试

```sh
python3 -m unittest discover -s tests -v
./uninstall.sh
```

测试覆盖 NVIDIA CSV 的 `N/A`、无 GPU JSON、异常值归一化、PowerShell 警告前缀 JSON 和缓存读取。真实连接测试由用户环境决定：`ssh y9000p-remote` 应先在 Mac 终端通过。

## 已知限制

GPU 指标依赖 Windows 端 NVIDIA 驱动提供的 `nvidia-smi`；网络是采集时刻的累计性能计数器速率；top 进程来自 Windows PerfFormattedData，排序粒度受 Windows 采样刷新影响。SwiftBar 菜单栏本身不适合绘制历史曲线。
