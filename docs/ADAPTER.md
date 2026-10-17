# 统一 QQ 适配器兼容性与手工验收

本插件面向 MaiBot 官方维护的**统一 QQ 连接器**（仓库 `MaiBot-SnowLuma-Adapter`），通过 Host 标准能力发送文件，不直接调用适配器 API。本文记录源码核实结论与宿主机验收步骤。

## 1. 适配器事实（源码核实）

核实对象：`D:\TemporaryFiles\MaiBot-SnowLuma-Adapter-main`（版本 1.1.7）。

| 事实 | 证据 |
|---|---|
| 1.0 起同一插件同时支持 SnowLuma/NapCat，连接后按 `get_version_info.app_name` 自动判定，平台标识统一 `qq` | `README.md:1-6`、`profile.py:126-191` |
| NapCat 画像：普通消息调 `send_group_msg`/`send_private_msg`，出站文件内联 `file` 段 | `profile.py:64-73`、`codecs/outbound/message_codec.py:147-176` |
| SnowLuma 画像：通用 `send_msg`，出站文件拆成独立 `upload_group_file`/`upload_private_file` | `profile.py:75-84`、`codecs/outbound/message_codec.py:215-258` |
| 文件引用只保留 `base64://`、`file://`、`http(s)://`，其他一律补 `file://` 前缀 | `codecs/outbound/segment_encoder.py:489-501` |
| 内联文件段读取 `data.file/path/url` 并携带 `name`；上传动作参数为 `{group_id|user_id, file, name?}` | `codecs/outbound/segment_encoder.py:413-443` |
| 网关以对端动作返回码判定成功，失败会回传 `success:false` | `plugin.py:851-935` |

结论：

- 本插件只调用 `ctx.send.custom("file", {"name","size","mime_type","url"}, stream_id)`，由统一适配器按画像分派；
- NapCat 为首选，SnowLuma 走同一代码路径；
- QQ 官方适配器不支持文件/语音出站，本插件不尝试适配（文件发送失败会回退分享链接）。

## 2. 传输选择

| 配置 | 行为 |
|---|---|
| `sending.mode=auto`（默认） | 文件 ≤ `base64_threshold_mb`（默认 8MB）→ `base64://`；否则 `local_path_enabled` 时复制到 `staging_dir` 并发 `file:///...`；都不可用 → 链接回退 |
| `sending.mode=base64` | 只走 Base64；超过阈值/10MiB IPC 上限则回退 |
| `sending.mode=local` | 只走共享路径；要求 MaiBot 与客户端同机可读 |
| `sending.mode=link` | 不发送文件，只发分享链接与本地路径 |

Host 侧约束（MaiBot 1.3.9 / SDK 2.14.0 源码核实）：

- SDK 没有 `send.file`，文件段必须通过自定义 `file` 类型发送；
- 文件引用必须放在 `url` 字段（`file` 字段会被 Host 当作文件名）；
- Base64 经插件 IPC 传输，总帧上限约 10 MiB；
- 组件调用默认 60s、RPC 默认 30s，因此下载/扫码均在后台任务完成。

## 3. 宿主机手工验收矩阵

> 本环境无法连接真实 QQ 客户端，以下项目需在部署 MaiBot 的机器上执行。

### 3.1 NapCat

| # | 操作 | 预期 |
|---|---|---|
| 1 | `/pan install` | 输出 BaiduPCS-Go 版本与路径，`bin/` 下出现可执行文件 |
| 2 | `/pan qrlogin` | 收到二维码图片；手机确认后提示登录成功 |
| 3 | `/pan https://pan.baidu.com/s/...` | 返回目录树，含文件大小与路径 |
| 4 | 小文件（≤8MB）`/pan file ...` | 聊天收到文件，日志显示 transport=base64 |
| 5 | 大文件（>8MB）`/pan file ...` | 聊天收到文件，日志显示 transport=local |
| 6 | 停止 NapCat 后再次下载 | 收到“文件发送失败”回退链接/本地路径，任务没有崩溃 |
| 7 | 群聊连续下载多项 | 按 `max_send_files` 截断，剩余列出本地路径 |

### 3.2 SnowLuma

重复 3.1 的 1–7；额外确认：

- 文件通过 `upload_group_file`/`upload_private_file` 成功上传（适配器日志可见动作名）；
- `file:///` 路径在 SnowLuma 侧可读（同机部署）。

### 3.3 权限与持久化

| # | 操作 | 预期 |
|---|---|---|
| 1 | 未配置 `access.admin_users` 时下载 | 提示“尚未配置管理员账号”，无任务启动 |
| 2 | 管理员 `/pan add <QQ号>` | 提示已加入白名单，`state/whitelist.json` 出现记录 |
| 3 | 白名单用户 `/pan file ...` | 可下载 |
| 4 | 普通用户 `/pan file ...` | 提示“没有下载权限” |
| 5 | 重启 MaiBot 后 `/pan wlist` | 白名单仍存在 |
| 6 | 退出登录后重启 | 需重新登录（登录态由 pcs_config.json 决定） |
| 7 | 下载过程中禁用插件 | 任务取消、子进程结束，无残留占用 |

### 3.4 异常路径

| 场景 | 预期 |
|---|---|
| 分享需要提取码但未提供 | 提示按 `/pan <链接> <提取码>` 重试 |
| 提取码错误 | 明确提示提取码错误 |
| 自己的分享 | 提示无需转存并列出网盘路径 |
| 网盘空间不足 | 提示空间不足，建议按项下载 |
| 超过 `transfer.max_file_size_mb` | 在转存前拒绝 |
| 镜像全部失败且未配置手动路径 | 提示检查网络/更换镜像/手动指定 |
| Base64 超限且禁用本地路径 | 回退分享链接文本 |

## 4. 常见问题

- **收不到文件但任务显示成功**：检查 `return_details.success` 日志与适配器日志；统一连接器需 1.0+，NapCat 文件段支持需较新版本。
- **`file:///` 发送失败**：MaiBot 与客户端不在同一台机器，或 `staging_dir` 不可读；改用 `sending.mode=link` 或提供共享目录。
- **QQ 官方适配器**：文件出站不支持，只能回退链接；请切换到统一 QQ 连接器。
- **群聊只收到部分文件**：群被动回复次数限制，调整 `max_send_files` 或改用私聊。
