# 百度网盘下载助手（MaiBot 插件）

把 AstrBot 插件 [`astrbot_plugin_baidu_pan`](https://github.com/linker9527/astrbot_plugin_baidu_pan) 的「分享浏览 → 精确转存 → BaiduPCS-Go 下载 → 发送到聊天」能力移植到 [MaiBot](https://docs.mai-mai.org/) 的第三方插件。

- 插件 ID：`blackbee.baidu-pan`
- 版本：`2.0.0`
- 作者：BlackBee114514
- 许可证：GPL-3.0（与原插件一致）

## 功能

- **免转存浏览**：分享目录走百度 Web API 直读，不占网盘空间。
- **精确转存**：下载时只转存选中的文件/目录，网盘空间不足时可按项下载。
- **BaiduPCS-Go 下载**：自动安装/校验可执行文件，支持镜像加速、进度上报、超时终止。
- **文件发送**：通过统一 QQ 连接器发送文件（NapCat / SnowLuma），失败回退分享链接。
- **登录方式**：二维码扫码（推荐）、BDUSS、Cookie、账号密码；登录态由 BaiduPCS-Go 持久化。
- **权限**：管理员 + 白名单 + 黑名单；白名单命令 `/pan add|del|wlist`，默认只允许管理员/白名单下载。
- **可观测**：轮转文件日志、下载进度、任务状态、脱敏日志；配置热更新。
- **LLM 工具**：`pan_list` / `pan_transfer` / `pan_download_dir` / `pan_download_all`。

> QQ 官方适配器不支持文件出站，本插件面向**统一 QQ 连接器**（NapCat 首选、SnowLuma 同样支持）。
> 文件发送依赖 Host 的自定义 `file` 消息段；发送失败会自动回退为分享链接，不会虚报成功。

## 环境要求

| 项目 | 要求 |
|---|---|
| MaiBot | `>= 1.3.0`（推荐 1.3.9+） |
| 插件 SDK | `maibot-plugin-sdk >= 2.14.0`（由宿主提供） |
| Python | `>= 3.12` |
| 适配器 | 统一 QQ 连接器（SnowLuma 或 NapCat），平台标识 `qq` |
| Python 依赖 | `httpx >= 0.27.0`（manifest 已声明，宿主自动安装） |
| 系统 | Windows 优先；Linux/macOS 支持 Linux/darwin 版 BaiduPCS-Go |

宿主会自动安装 manifest 中声明的 Python 依赖（优先使用宿主 `pyproject.toml [tool.uv]` 配置的国内镜像）。

## 安装

1. 将本仓库目录放到 MaiBot 的 `plugins/baidu-pan/`（或在插件市场安装）。
2. 启动 MaiBot，首次加载会在插件目录生成 `config.toml`（默认配置）。
3. 在 WebUI 插件配置页填写 `[access].admin_users`（管理员 QQ，例如 `qq:123456`）。
4. 在聊天中执行 `/pan install` 安装 BaiduPCS-Go。
5. 执行 `/pan qrlogin` 扫码登录百度网盘。
6. 管理员用 `/pan add <QQ号>` 将需要下载的成员加入白名单。

## 快速开始

```text
/pan install                    # 安装/更新 BaiduPCS-Go
/pan qrlogin                    # 扫码登录（管理员）
/pan https://pan.baidu.com/s/1xxxx?pwd=abcd   # 浏览分享目录
/pan file 文件夹/文件.zip        # 下载文件并发送（白名单）
/pan dir 文件夹                  # 下载整个目录（白名单）
/pan add 123456                 # 添加白名单（管理员）
/pan wlist                      # 查看白名单（管理员）
/pan help                       # 帮助
```

## 配置说明

配置文件：`plugins/baidu-pan/config.toml`（Runner 首次加载自动生成；保存后热更新）。
`.gitignore` 已忽略 `/config.toml`、`/logs/`、`/data/`，请勿把配置或日志提交到仓库。

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `plugin.enabled` | `true` | 插件总开关 |
| `plugin.config_version` | `"2.0.0"` | 配置版本（请勿手改） |
| `access.admin_users` | `""` | 管理员，逗号分隔，支持 `qq:123` 或裸 `123`；为空时敏感命令与下载全部不可用 |
| `access.whitelist_enabled` | `true` | 下载/转存是否需要管理员或白名单 |
| `access.admin_only_sensitive` | `true` | 登录/安装/状态/白名单管理是否仅管理员 |
| `access.blacklist` | `""` | 黑名单，逗号分隔；命中后命令静默、工具提示无权限 |
| `share.cache_ttl_minutes` | `30` | 分享会话缓存时长 |
| `share.max_depth` | `3` | 目录树最大深度 |
| `share.max_items` | `300` | 单次目录树最大节点数 |
| `share.entries_per_dir` | `60` | 每个目录展示条数 |
| `transfer.cloud_save_dir` | `/我的资源/AutoTransfer` | 转存目标目录 |
| `transfer.auto_delete_cloud` | `true` | 发送成功后删除云端临时文件 |
| `transfer.max_file_size_mb` | `0` | 单文件/目标总量上限（MB），0 为不限；超限在转存前拒绝 |
| `transfer.verify_after_transfer` | `true` | 转存后复查目标文件 |
| `download.baidu_pcs_go_path` | `""` | 手动指定 BaiduPCS-Go 路径；留空使用自动安装版本 |
| `download.pcs_version` | `v4.0.2` | 自动安装版本（内置校验哈希） |
| `download.pcs_download_prefixes` | `https://gh-proxy.com/,https://git.yylx.win/` | 镜像前缀，逗号分隔；失败后回退 GitHub 直连 |
| `download.pcs_verify_checksum` | `true` | 校验大小与 SHA256 |
| `download.download_dir` | `""` | 下载目录；留空为插件 data 目录下 `downloads` |
| `download.max_concurrent_jobs` | `1` | 同时下载任务数 |
| `download.timeout_seconds` | `0` | 0 自动（单文件 max(600, MB×15)，目录 3600）；>0 固定秒数 |
| `download.progress_enabled` | `false` | 是否发送进度消息 |
| `download.progress_interval_seconds` | `30` | 进度推送间隔 |
| `download.cleanup_hour` | `3` | 每天本地时间几点清理；`-1` 关闭 |
| `download.retention_hours` | `24` | 下载文件保留时长 |
| `sending.mode` | `auto` | `auto` / `base64` / `local` / `link` |
| `sending.base64_threshold_mb` | `8` | 小于等于该值走 Base64（Host IPC 硬上限 10 MiB） |
| `sending.staging_dir` | `""` | 大文件共享暂存目录；留空为插件 data 目录下 `upload_cache`，必须对 NapCat/SnowLuma 可读 |
| `sending.local_path_enabled` | `true` | 是否允许 `file:///` 发送大文件 |
| `sending.link_fallback` | `true` | 发送失败时回退分享链接 |
| `sending.max_send_files` | `10` | 单任务最多发送文件数，超出只列本地路径 |
| `account.bduss` / `account.stoken` | `""` | 可选：启动自动登录凭据（一般无需填写，扫码登录已持久化） |
| `account.auto_login` | `false` | 启动时是否用上述凭据自动登录 |
| `logging.level` | `INFO` | 日志级别 |
| `logging.file_enabled` | `true` | 是否写轮转日志文件 |
| `logging.file_max_bytes` | `2097152` | 单日志文件大小 |
| `logging.file_backup_count` | `3` | 日志轮转保留数 |

### 持久化位置

| 内容 | 路径（相对 MaiBot 根目录） |
|---|---|
| 插件配置 | `plugins/baidu-pan/config.toml` |
| 白名单 | `data/plugins/blackbee.baidu-pan/state/whitelist.json` |
| 下载文件清单 | `data/plugins/blackbee.baidu-pan/state/cleanup_manifest.json` |
| BaiduPCS-Go 登录态 | `data/plugins/blackbee.baidu-pan/pcs-config/pcs_config.json` |
| BaiduPCS-Go 可执行文件 | `data/plugins/blackbee.baidu-pan/bin/BaiduPCS-Go[.exe]` |
| 下载文件 | `data/plugins/blackbee.baidu-pan/downloads/` |
| 发送暂存副本 | `data/plugins/blackbee.baidu-pan/upload_cache/` |
| 日志 | `data/plugins/blackbee.baidu-pan/logs/baidu-pan.log` |

## 命令

`/pan` 是单入口命令，子命令如下：

| 命令 | 权限 | 说明 |
|---|---|---|
| `/pan help` | 所有人 | 帮助 |
| `/pan <链接或surl> [提取码]` | 所有人 | 解析并浏览分享目录树 |
| `/pan ls [链接] [提取码]` | 所有人 | 刷新当前分享（带链接则切换分享） |
| `/pan look` | 所有人 | 查看当前缓存的目录树 |
| `/pan file <路径>`（别名 `f`） | 管理员/白名单 | 下载单个文件并发送 |
| `/pan dir <路径>`（别名 `folder`/`fl`） | 管理员/白名单 | 下载整个目录并发送 |
| `/pan install [force]`（别名 `download`） | 管理员 | 安装/更新 BaiduPCS-Go（`force` 强制重装） |
| `/pan qrlogin` | 管理员 | 扫码登录（后台轮询，二维码约 3 分钟有效） |
| `/pan bduss <BDUSS> [STOKEN]` | 管理员 | BDUSS 登录 |
| `/pan cookies <Cookie>` | 管理员 | Cookie 登录 |
| `/pan login <账号> <密码>` | 管理员 | 账号密码登录（不推荐；密码会出现在聊天记录） |
| `/pan unlogin` | 管理员 | 退出登录 |
| `/pan status` | 管理员（或白名单，见配置） | 查看运行状态 |
| `/pan add <QQ号或@成员>` | 管理员 | 添加白名单 |
| `/pan del <QQ号或@成员>` | 管理员 | 移除白名单 |
| `/pan wlist` | 管理员 | 列出白名单 |

权限说明：

- **管理员**：`access.admin_users` 中的账号，始终可以下载与管理。
- **白名单**：`/pan add` 添加的账号，可下载/转存。
- **普通用户**：可浏览分享、使用 `/pan help`，不能下载。
- 黑名单优先级最高：命令静默忽略，工具提示无权限。
- 未配置管理员时，白名单命令和下载都不可用，请先在配置页填写管理员。

## LLM 工具

| 工具 | 参数 | 说明 | 权限 |
|---|---|---|---|
| `pan_list` | `link`、`pwd` | 浏览分享目录树，返回文本 | 非黑名单 |
| `pan_transfer` | `link`、`pwd`、`path` | 只转存到 `cloud_save_dir`，不下载 | 管理员/白名单 |
| `pan_download_dir` | `folder_path`、`link`、`pwd` | 转存 + 后台下载 + 发送 | 管理员/白名单 |
| `pan_download_all` | `link`、`pwd` | 下载分享根目录全部内容 | 管理员/白名单 |

下载类工具立即返回受理信息，实际文件与结果由后台任务发送到当前聊天。

## 文件发送与适配器

发送契约（已按统一 QQ 连接器 1.1.7 源码核实）：

```python
await self.ctx.send.custom(
    "file",
    {"name": 文件名, "size": 字节数, "mime_type": mime, "url": "base64://..." 或 "file:///..."},
    stream_id,
    storage_message=False,
    show_log=False,
    sync_to_maisaka_history=False,
    return_details=True,
)
```

- `NapCat`：适配器把文件段内联为 OneBot `file` 消息段。
- `SnowLuma`：适配器拆成独立的 `upload_group_file` / `upload_private_file` 动作。
- 返回结果只有 `success == True` 才视为发送成功，否则回退分享链接文本。
- Base64 走 Host IPC，硬上限约 10 MiB；默认阈值 8MB，超出自动改走共享路径。
- `file:///` 大文件要求 MaiBot 与 NapCat/SnowLuma 同机，且 `sending.staging_dir` 对客户端可读；跨机部署请改 `sending.mode=link` 或提供共享目录。
- 群聊存在被动回复次数限制，单任务最多发送 `sending.max_send_files` 个文件，其余在结果中列出本地路径。

## 日志与排错

- 日志同时输出到 MaiBot 日志与 `data/plugins/blackbee.baidu-pan/logs/baidu-pan.log`（轮转）。
- 需要详细排查时把 `logging.level` 改为 `DEBUG` 并保存配置（热更新）。
- 日志不会记录 BDUSS、Cookie、密码等凭据；命令输出中的敏感字段会被脱敏。
- 常见问题：
  - **提示尚未配置管理员**：在配置页填写 `access.admin_users`，例如 `qq:123456`。
  - **提示没有下载权限**：管理员执行 `/pan add <QQ号>` 将你加入白名单。
  - **发送失败/文件过大**：确认统一 QQ 连接器版本，检查 `sending.staging_dir` 是否同机可读；或设置 `sending.mode=link` 回退链接。
  - **BaiduPCS-Go 下载失败**：检查镜像前缀是否可用，或手动下载后填入 `download.baidu_pcs_go_path`。
  - **分享链接失效/需要提取码**：重新发送 `/pan <链接> <提取码>`；自己的分享会提示无需转存。
  - **百度接口返回失败**：可能是 Cookie 过期或百度风控，重新 `/pan qrlogin` 登录。

## 开发与测试

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-dev.txt
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python scripts\selfcheck.py
.\.venv\Scripts\python -m json.tool _manifest.json
```

- 全部单元测试离线运行（httpx MockTransport + 假 ctx/子进程），不访问真实百度与 QQ。
- 真实链路验收见 [`docs/ADAPTER.md`](docs/ADAPTER.md)；协议与错误码见 [`docs/PROTOCOL.md`](docs/PROTOCOL.md)；实施计划与进度见 [`docs/PLAN.md`](docs/PLAN.md)。

## 安全与隐私

- 插件不内置、不打包 BaiduPCS-Go 二进制，安装时从官方 GitHub Release 经镜像下载并校验 SHA256。
- 扫码登录凭据只写入本机 BaiduPCS-Go 配置，不上传任何服务器。
- 配置、白名单、日志、下载数据全部位于 `data/` 或插件目录，已在 `.gitignore` 排除。
- 白名单文件权限 0600，采用原子写；损坏时自动备份后重建。
- 管理员/白名单控制只用于共享下载能力，请自行评估群成员范围。

## 许可证与致谢

- 本插件基于 [linker9527/astrbot_plugin_baidu_pan](https://github.com/linker9527/astrbot_plugin_baidu_pan)（GPL-3.0）移植，遵循 GPL-3.0。
- BaiduPCS-Go 作者：[qjfoidnh](https://github.com/qjfoidnh/BaiduPCS-Go)。
- MaiBot 插件规范：<https://docs.mai-mai.org/plugin/>。
