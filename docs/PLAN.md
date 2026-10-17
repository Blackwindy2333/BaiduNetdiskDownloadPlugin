# 百度网盘下载助手（MaiBot 插件）v2.0.0 实施计划

> 状态：已批准，执行中。
> 本文档是实现与验收的唯一计划来源；每次功能提交后更新「进度」与「决策记录」。

## 进度

- [x] 1. 插件骨架、配置模型与日志系统
- [x] 2. 管理员白名单与权限校验
- [x] 3. 分享解析、目录浏览与会话缓存
- [x] 4. BaiduPCS-Go 镜像安装与账号登录
- [x] 5. 分享文件精确转存
- [ ] 6. 下载任务、进度通知与文件发送
- [ ] 7. 帮助、清理任务与项目文档

## 1. 目标与验收标准

将 AstrBot 插件 `astrbot_plugin_baidu_pan` 移植为 MaiBot 插件，保留「分享链接浏览 → 精确转存 → BaiduPCS-Go 下载 → 发送到聊天」主链路，并按新需求加入管理员/白名单、镜像下载、统一 QQ 适配器（NapCat 首选、SnowLuma 同一路径）支持。插件 id `blackbee.baidu-pan`，版本 `2.0.0`，作者 `BlackBee114514`。

验收标准：

- 仓库根即插件根，包含 `_manifest.json`、`plugin.py`、`README.md`、`config.py`、`baidu_pan/`、`tests/`、`docs/`、`.gitignore`。
- `config.toml` 由 SDK Runner 首次加载自动生成默认值，插件启动时检查并记录；whitelist 状态文件首次启动自动创建。
- `/pan help`、链接浏览、`/pan add|del|wlist`、`/pan qrlogin|bduss|cookies|login|unlogin`、`/pan dir|file`、`/pan download`、四个 LLM 工具可用；长任务不阻塞 Host RPC。
- 文件发送统一走 `ctx.send.custom("file", {...})`，NapCat/SnowLuma 均由统一适配器分派；发送失败回退链接文本，不虚报成功。
- BaiduPCS-Go 仅从配置的镜像/直连下载，SHA256+大小校验后安装；不内置二进制、不依赖 Go。
- `py -3.14 -m pytest` 全绿；`python -m json.tool _manifest.json` 通过；`git status --short` 干净；7 个 `feat:` 提交；配置/日志/下载数据不进 git。
- `docs/PLAN.md` 写入本计划，并在每次提交更新进度；`docs/ADAPTER.md` 给出宿主机手工验收矩阵（本环境无法连真实 QQ 客户端，需用户侧验证）。

## 2. 适配器结论（已读源码核实）

源码：`D:\TemporaryFiles\MaiBot-SnowLuma-Adapter-main`（统一 QQ 连接器 1.1.7）。

| 事实 | 证据 |
|---|---|
| 1.0 起同一插件同时支持 SnowLuma/NapCat，连接后按 `get_version_info.app_name` 自动判定，平台标识统一 `qq` | `README.md:1-6`、`profile.py:126-191` |
| NapCat 画像：普通消息 + 内联 `file` 段；SnowLuma 画像：`send_msg` + 独立 `upload_group_file`/`upload_private_file` | `profile.py:64-84`、`codecs/outbound/message_codec.py:147-176,215-258` |
| 文件引用只保留 `base64://`、`file://`、`http(s)://`，其他一律补 `file://` 前缀 | `codecs/outbound/segment_encoder.py:489-501` |
| 内联文件段读取 `data.file/path/url`，并带 `name`；SnowLuma 上传动作参数为 `{group_id|user_id, file, name?}` | `codecs/outbound/segment_encoder.py:413-443`、`message_codec.py:235-258` |
| 网关发送成功以对端动作返回码为准，失败会把 `success:false` 回传 Host | `plugin.py:851-935` |

结论：**本插件不直接调用 `adapter.napcat.*` / `adapter.snowluma.*` API**，只使用 Host 标准能力 `send.custom("file", payload)`（payload `{name,size,mime_type,url}`，`url` 为 `base64://`、`file:///` 或链接），由统一适配器按画像分派；一条代码路径同时覆盖 NapCat 与 SnowLuma。QQ 官方适配器不支持文件/语音出站，明确不在范围内。

宿主侧约束（已核实 MaiBot 1.3.9 / SDK 2.14.0）：

- `send.custom` 无 `send.file`，文件必须放在 `url` 字段；`data` 必须是 dict；不能把 base64 放进 `file` 字段（Host 会把 `file` 当作文件名）。
- Base64 经 IPC 传输硬上限 10 MiB；小文件用 `base64://`，大文件用共享路径 `file:///...`，NapCat/SnowLuma 需与 MaiBot 同机可读；失败回退链接。
- 单次组件调用默认 60 s、RPC 30 s：二维码轮询、下载、发送全部放后台 `asyncio.Task`，命令/工具立即返回受理提示。

## 3. 架构与模块

```text
_manifest.json
plugin.py                 # MaiBotPlugin：生命周期、命令/工具注册、权限分发、help
config.py                 # PluginConfigBase 配置模型（分节 + UI 元数据）
baidu_pan/
  __init__.py
  __version__.py          # 插件 id/名称/版本常量
  models.py               # ShareInfo/ShareNode/ShareTree/DownloadJob/SendOutcome 等 dataclass
  errors.py               # 错误类型 + 中文错误文案
  logging_utils.py        # ctx.logger + RotatingFileHandler，脱敏
  config_file.py          # 配置文件检查与默认 TOML 生成
  access.py               # 身份解析、管理员/白名单/黑名单判定、白名单持久化
  account.py              # 读取 BaiduPCS-Go 登录态（pcs_config.json → Cookie）
  texts.py                # 用户可见中文文案（帮助/提示）
  utils.py                # 大小格式化、路径安全、原子 JSON、ID 解析
  share.py                # 分享解析、密码校验、目录树构建与渲染、按 stream_id 的会话缓存
  qrlogin.py              # Baidu Passport 二维码获取/轮询/Cookie 换取
  transfer.py             # 转存、errno 映射、空间/冲突处理、自家分享直下
  pcs_installer.py        # 镜像下载、大小+SHA256 校验、解压、安装
  pcs_client.py           # BaiduPCS-Go 子进程封装：env/CLI/超时/输出解析
  download_manager.py     # 任务队列、并发、超时、进度、取消、文件发现
  sender.py               # base64/本地共享路径/链接回退，success=true 判定
  cleanup.py              # 下载文件清单 + 定时清理
tests/
  conftest.py             # 假 ctx/logger/paths/send、httpx MockTransport
  test_manifest.py test_config.py test_config_file.py test_lifecycle.py test_access.py
  test_share.py test_transfer.py test_pcs_installer.py
  test_pcs_client.py test_qrlogin.py test_download_manager.py test_sender.py test_commands.py
docs/PLAN.md docs/ADAPTER.md docs/PROTOCOL.md
README.md .gitignore requirements-dev.txt pytest.ini
```

实现说明：为减少无意义分层，`baidu_api.py`/`baidu_parser.py` 的职责并入 `share.py`，会话缓存由 `ShareSessionCache` 承担；二维码登录独立为 `qrlogin.py`。

模块只通过显式构造注入依赖（config/access/api/pcs/sender），不读全局单例，便于后续加功能与测试。

## 4. 配置模型（config.toml，默认值即推荐值）

沿用参考插件的 `PluginConfigBase` + 分节 `BaseModel` + `__ui_label__/__ui_icon__/__ui_order__`。敏感字段加 `json_schema_extra={"secret": True}`。

| 节.字段 | 默认 | 说明 |
|---|---|---|
| `plugin.enabled` | `true` | 插件总开关 |
| `plugin.config_version` | `"2.0.0"` | Runner 维护/配置迁移 |
| `access.admin_users` | `""` | 逗号分隔，支持 `qq:123` 或裸 `123`；空=无管理员，敏感命令与白名单管理不可用 |
| `access.whitelist_enabled` | `true` | 下载类请求需管理员或白名单 |
| `access.admin_only_sensitive` | `true` | 登录/安装/卸载/状态/白名单管理仅管理员 |
| `access.blacklist` | `""` | 原插件黑名单，命中命令静默、工具返回无权限 |
| `share.cache_ttl_minutes` | `30` | 分享会话缓存时长 |
| `share.max_depth` | `3` | 目录树最大深度（原插件 3） |
| `share.max_items` | `300` | 单次树节点上限 |
| `share.entries_per_dir` | `60` | 每目录展示上限（接口分页拉取后截断展示） |
| `transfer.cloud_save_dir` | `"/我的资源/AutoTransfer"` | 转存目标，必须以 `/` 开头且不含 `..` |
| `transfer.auto_delete_cloud` | `true` | 发送/下载成功后删除网盘临时文件 |
| `transfer.max_file_size_mb` | `0` | 0=不限；超限在转存/下载前拒绝 |
| `transfer.verify_after_transfer` | `true` | 转存后复查文件是否在目标目录 |
| `download.baidu_pcs_go_path` | `""` | 留空用 `data_dir/bin/BaiduPCS-Go[.exe]`；也可手填绝对路径 |
| `download.pcs_version` | `"v4.0.2"` | 仅内置该版本哈希表；其他版本需可获取 SHA256，否则拒绝自动安装 |
| `download.pcs_download_prefixes` | `"https://gh-proxy.com/,https://git.yylx.win/"` | 逗号分隔，按序尝试后回退 GitHub 直连 |
| `download.pcs_verify_checksum` | `true` | 大小+SHA256 校验；关闭也校验 ZIP/大小并告警 |
| `download.download_dir` | `""` | 留空=`data_dir/downloads`；允许绝对路径（操作者配置，需无 `..`） |
| `download.max_concurrent_jobs` | `1` | 并发下载数，避免百度风控 |
| `download.timeout_seconds` | `0` | 0=自动（单文件 max(600, MB×15)，目录 3600） |
| `download.progress_enabled` | `false` | 进度消息开关 |
| `download.progress_interval_seconds` | `30` | 进度节流 |
| `download.cleanup_hour` | `3` | 每日清理时刻；-1 关闭 |
| `download.retention_hours` | `24` | 下载文件保留时长 |
| `sending.mode` | `"auto"` | `auto`=≤阈值 base64，否则本地路径，再否则链接；`base64`/`local`/`link` 强制 |
| `sending.base64_threshold_mb` | `8` | 上限 10（Host 硬限制），越界自动降级 |
| `sending.staging_dir` | `""` | 留空=`data_dir/upload_cache`；须为 NapCat/SnowLuma 可读的共享路径 |
| `sending.local_path_enabled` | `true` | 允许 `file:///` 大文件传输 |
| `sending.link_fallback` | `true` | 发送失败时发分享链接+提取码 |
| `sending.max_send_files` | `10` | 单任务最多发送文件数，超出列本地路径清单（规避群被动回复上限） |
| `account.bduss` / `account.stoken` | `""` | 可选启动自动登录，secret；平时登录凭据由 BaiduPCS-Go 自己的 pcs_config.json 持久化 |
| `account.auto_login` | `false` | 启动时用上述凭据登录 |
| `logging.level` | `"INFO"` | DEBUG/INFO/WARNING/ERROR |
| `logging.file_enabled` | `true` | 写 `data_dir/logs/baidu-pan.log` |
| `logging.file_max_bytes` | `2097152` | 轮转大小 |
| `logging.file_backup_count` | `3` | 轮转保留数 |

配置持久化：Runner 负责首次生成 `config.toml` 与缺省字段补齐；`on_load` 再检查一次并记录路径/缺失告警，打印非敏感摘要。`config.toml`、`logs/`、`data/`、`.venv/` 全部进 `.gitignore`。

## 5. 权限与白名单

- 管理员：`access.admin_users`，按 `platform:user_id` 匹配；裸数字默认 `qq`。平台统一为 `qq`（统一适配器）。
- 白名单：`data_dir/state/whitelist.json`，结构 `{"version":1,"users":[{"platform":"qq","user_id":"...","added_by":"...","added_at":"ISO"}]}`；原子写（temp+replace）、文件锁、坏文件备份为 `.bak` 后重建、权限 0600。
- 判定顺序：黑名单 → 管理员 → 白名单开关（关闭则放行普通用户）→ 白名单 → 拒绝。
- 命令 `/pan add <id|@成员>`、`/pan del <id|@成员>`、`/pan wlist` 仅管理员；`add` 支持从消息 `at` 段取 QQ，也接受纯数字；重复/不存在给出明确中文提示。
- 登录、二维码、bduss/cookies、login/unlogin、install/download、status 默认仅管理员（`admin_only_sensitive` 可关，关闭后放行白名单用户）。
- 下载/转存类命令与 `pan_transfer/pan_download_dir/pan_download_all` 工具需管理员或白名单；浏览类命令与 `pan_list` 对非黑名单用户开放。
- 未配置管理员时：敏感命令与白名单命令提示「请先在后台配置管理员账号」，避免插件处于无人可管的半配置状态。
- 发送者 ID 提取：优先命令/工具 kwargs 的 `user_id`，兼容 `message.user_id`、`message_info.user_info.user_id`、`sender.user_id`、`message_base_info`；提取失败时下载类请求明确拒绝，不猜测。

## 6. 命令规范

单入口 `@Command("pan", pattern=r"^/pan(?:\s+(?P<args>[\s\S]*))?$")`，子命令由处理器分发；返回 `(success, response, weight)`。全部简体中文、短消息，树内容超长截断并提示。

| 语法 | 权限 | 行为 |
|---|---|---|
| `/pan help` | 所有非黑名单 | 帮助页（命令、权限说明、示例） |
| `/pan <链接或surl> [提取码]` | 浏览权限 | 解析并缓存分享，回复根目录树/文件信息 |
| `/pan ls [链接] [提取码]` | 浏览权限 | 有链接则重新解析并缓存；无链接用当前会话缓存 |
| `/pan look` | 浏览权限 | 显示当前会话缓存的目录树；无缓存给出引导 |
| `/pan dir\|folder\|fl <路径>` | 管理员/白名单 | 精确匹配当前分享中的目录 → 转存 → 下载 → 发送；后台任务 |
| `/pan file\|f <路径>` | 管理员/白名单 | 同上，目标为文件 |
| `/pan install\|download [--git]` | 管理员 | 安装/更新 BaiduPCS-Go（默认镜像链；`--git` 直连 GitHub），回复版本与校验结果 |
| `/pan qrlogin` | 管理员 | 后台获取二维码 PNG → `ctx.send.image(base64)`；轮询扫码 → 登录 → 保存凭据 |
| `/pan bduss <BDUSS> [STOKEN]` | 管理员 | 写入并验证登录；凭据不回显、不写日志 |
| `/pan cookies <cookie>` | 管理员 | 清洗后 `login -cookies=`；失败回退解析 BDUSS/STOKEN |
| `/pan login <账号> <密码>` | 管理员 | `login -username= -password=`（不落日志）；失败提示改用二维码 |
| `/pan unlogin` | 管理员 | `logout -y` 并清理缓存状态 |
| `/pan status` | 管理员 | 登录账号/uid、运行中任务、白名单数量、安装版本、发送模式 |
| `/pan add <id\|@成员>` / `/pan del <id\|@成员>` / `/pan wlist` | 管理员 | 白名单维护与列出（最多展示 50 条+总数） |

命令模式全部锚定 `^...$`；参数解析容错空格、全角空格；未知子命令回复帮助摘要。

## 7. LLM 工具规范

保留原插件四个工具名与参数，描述写清「何时用、参数、限制、副作用」，返回 `{"content": 文本}`；异常在工具内捕获为可读中文，绝不返回堆栈或 base64 正文。

| 工具 | 参数 | 行为/副作用 |
|---|---|---|
| `pan_list` | `link=""`, `pwd=""` | 获取/刷新分享目录树，返回文本树；写当前 stream 会话缓存；无下载副作用 |
| `pan_transfer` | `link=""`, `pwd=""`, `path=""` | 精确匹配并转存到 `cloud_save_dir`，返回目标路径/大小；需白名单；不发送文件 |
| `pan_download_dir` | `folder_path`, `link=""`, `pwd=""` | 转存+下载目录+发送文件；后台任务；立即返回受理信息；需白名单 |
| `pan_download_all` | `link=""`, `pwd=""` | 根目录全部内容；其余同上；需白名单 |

工具并发去重键 `(stream_id, target_path)`；重复请求返回「任务已在进行」。

## 8. 百度网盘协议与源码缺陷修复

按原插件协议实现并保留页面兜底：

- `shorturlinfo`（appid=250528, root=1, Referer=disk/main）取 shareid/uk；errno -9 需提取码。
- `gettemplatevariable` 取 bdstoken；`share/verify` 校验提取码（errno -9=提取码错误）。
- `share/list` 分页（`num=100&page=n`）拉取，字段 `server_filename/isdir/fs_id/size/path`；树深度/条目上限按配置。
- `share/transfer`：`fsidlist` JSON 数组字符串 + `path`；errno 0 成功、4 已转存视为成功、12 冲突读取 `info[]`（全 -30/0 成功，-32/空间不足 报空间错误）、2 自家分享不再误判成功。
- 修复原插件缺陷：
  1. errno 2（自家分享）→ 通过 `pcs who` 的 uid 与 share uk 判定，改为直接下载原路径，不再转存后找不到文件；
  2. `max_file_size_mb` 在转存/下载前检查（文件大小或目录树合计），避免「先转存再拒绝」；
  3. `/pan dir|file` 与工具统一走发送策略，不再固定发原始 File 组件、不再绕过大小限制；
  4. 进度百分比修正（原 `%%` 双百分号）；
  5. 分享缓存按 `stream_id` 隔离并 TTL/LRU，不再全局串话/永久缓存；
  6. 自家分享、空间不足、提取码错误、cookie 失效给出可操作中文提示。
- 链接解析：`pan.baidu.com/s/([A-Za-z0-9_-]+)`、裸 surl、`?pwd=`、`提取码:xxxx`、`链接 052x`；统一规范化后缓存。

## 9. BaiduPCS-Go 集成、镜像与登录

- 配置目录：`data_dir/pcs-config`，通过环境变量 `BAIDUPCS_GO_CONFIG_DIR` 传给所有子进程；登录凭据持久化在该目录的 `pcs_config.json`，重启仍有效。
- 安装（v4.0.2 固定哈希表）：
  - 资源名按平台/架构映射（windows-x64/arm64、linux-amd64/arm64、darwin-osx-amd64/arm64），URL `https://github.com/qjfoidnh/BaiduPCS-Go/releases/download/{version}/{asset}`。
  - 按 `pcs_download_prefixes` 顺序拼 `prefix + 完整 GitHub URL` 下载，最后直连；已实测 `gh-proxy.com` 与 `git.yylx.win` 均返回真实 ZIP。
  - 校验：Content-Length/实际字节数 + SHA256（v4.0.2 六个平台哈希已核实），解压只取 `BaiduPCS-Go(.exe)`，原子移动到 `data_dir/bin/`，`--version` 复验。
  - Windows 下载后 `CREATE_NO_WINDOW`；失败清理临时文件并提示镜像/手动 `baidu_pcs_go_path`。
- 子进程统一封装：UTF-8 解码（errors=replace）、超时、终止、输出行回调；成功判定不依赖退出码（BaiduPCS-Go 退出码大多被丢弃），以「下载结束」/目标文件存在/无「以下文件下载失败」综合判定。
- 登录：
  - 二维码：`getqrcode` → 下载 PNG → `ctx.send.image` → 每 3 s 轮询 `channel/unicast` 60 次 → `qrbdusslogin` 收集 cookie → `login -cookies=`，失败回退 `login -bduss=`；二维码任务可取消，3 分钟提示过期。
  - BDUSS/STOKEN、cookies、账号密码按原插件流程；密码仅走命令行参数，不写日志、不回显。
  - `who` 查询 uid/用户名，缓存 60 s；`logout -y` 清理。
- 下载：`download --ow --saveto <download_dir> --mode locate <云路径>`；单文件/目录超时按配置或自动；1 Hz 解析进度行并节流上报；取消时终止子进程；下载完成后定位文件（预期路径 → 目录遍历 → 最新文件），校验大小。
- 清理：`data_dir/state/cleanup_manifest.json` 记录插件下载/暂存文件；每日 `cleanup_hour` 删除超 `retention_hours` 的文件；只删清单内路径，`upload_cache` 发送成功后立即删、失败保留 24 h；`auto_delete_cloud` 在发送成功后 `rm <云路径>`。

## 10. 文件发送策略（sender）

1. 计算大小与 MIME（按扩展名）。
2. `mode=auto`：≤`base64_threshold_mb`（且 ≤10 MiB）→ `url=base64://<b64>`；否则若 `local_path_enabled` → 复制到 `staging_dir/<job>/<安全文件名>`，`url=Path(...).as_uri()`（Windows 形如 `file:///D:/...`），只读副本；否则链接。
3. 调用：

   ```python
   await ctx.send.custom(
       "file",
       {"name": name, "size": size, "mime_type": mime, "url": url},
       stream_id,
       storage_message=False,
       show_log=False,
       sync_to_maisaka_history=False,
       return_details=True,
   )
   ```

4. 仅当返回值为 dict 且 `success is True` 判成功（宿主/适配器约定）；否则记录动作/错误并回退：`link_fallback` 发分享链接+提取码，仍失败则发本地路径。
5. 发送顺序：开始提示（可选）→ 文件（最多 `max_send_files` 个）→ 结果摘要（文件名/大小/耗时/本地路径）。群聊注意被动回复上限，超出改列清单。
6. 文件发送不阻塞下载任务；所有发送在后台任务内完成，异常不外抛到 Host。

## 11. 生命周期、日志与持久化

- `on_load`：初始化 logger（ctx.logger + 文件 handler）、检查 `config.toml` 与 whitelist 文件、初始化 access/session/download manager、按需自动登录、记录版本与发送模式；不在此做网络长任务。
- `on_unload`：设置停止标志 → 取消二维码/下载/清理任务 → `terminate/kill` 子进程 → 关闭 httpx client → flush/detach 文件 handler；可重复调用不报错。
- `on_config_update`：仅 `scope=="self"` 时应用；刷新日志级别、access 管理员、发送/下载参数；不中断运行中任务（路径变更只影响新任务）；记录脱敏 diff 摘要。
- `create_plugin()` 返回实例；导入模块时无副作用；所有依赖延迟到 on_load/首次使用。
- 日志：命令/工具入口（用户、stream 摘要、动作）、HTTP 状态/errno（不含 cookie/bduss/密码）、任务状态、PCS 命令行（脱敏）、发送结果；文件日志轮转；敏感字段统一 `***`。
- 持久化：`config.toml`（Runner）、`state/whitelist.json`、`state/cleanup_manifest.json`、`pcs-config/pcs_config.json`、`logs/`；均位于 `data_dir`，`temp_dir` 只放临时下载中间文件。

## 12. 测试与验证

- 纯单元/异步测试，不访问真实百度/GitHub：httpx `MockTransport` 模拟各接口与错误码；假 `ctx`（logger/paths/send/command kwargs）参考 `DeepSeekSearchPlugin/tests/conftest.py` 模式。
- 覆盖：manifest 字段/能力；配置默认值与校验；生命周期卸载幂等；权限矩阵与白名单原子写/坏文件恢复；链接/提取码解析；树构建与分页；转存 errno（0/4/2/12/-32）；自家分享直下；镜像 URL 拼接 + SHA256/大小校验（含伪造 ZIP 拒绝）；CLI 参数顺序与输出解析；下载超时/取消/文件发现；sender 三种 payload 与 success=false 回退；命令/工具返回值与中文错误；无敏感信息进日志。
- 本地命令：

  ```powershell
  py -3.14 -m venv .venv
  .\.venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-dev.txt
  .\.venv\Scripts\python -m pytest
  ```

  `requirements-dev.txt` 注明清华镜像及阿里/腾讯备选；manifest 依赖 `httpx>=0.27.0` 由宿主机 uv（阿里优先）安装。
- 每完成一个功能：跑全量测试 → 静态检查（`json.tool`、`compileall`）→ 提交；提交前 `git status --short` 确认无配置/日志/数据。
- `docs/ADAPTER.md` 手工验收矩阵（用户宿主机执行）：NapCat 小文件 base64、大文件共享路径；SnowLuma 同样两项；停客户端后失败回退链接；二维码图片；双 QQ 号白名单拦截；重启后配置/白名单/登录持久化；卸载中取消下载无僵尸进程。

## 13. 提交计划（main 分支，逐功能提交）

1. `feat: 搭建插件骨架、配置模型与日志系统` — `_manifest.json`、`plugin.py`（生命周期/help/命令分发骨架）、`config.py`、`logging_utils.py`、`.gitignore`、`requirements-dev.txt`、`pytest.ini`、`docs/PLAN.md`、骨架测试。
2. `feat: 实现管理员白名单与权限校验` — `access.py`、whitelist store、`add/del/wlist/status`、权限矩阵、测试。
3. `feat: 实现分享解析、目录浏览与会话缓存` — 解析/API/树/session、`ls/look/默认链接`、测试（`pan_list` 工具随第 7 项统一接入）。
4. `feat: 集成 BaiduPCS-Go 镜像安装与账号登录` — installer/client、`install/download/qrlogin/bduss/cookies/login/unlogin`、测试。
5. `feat: 实现分享文件精确转存` — transfer、`pan_transfer`、errno 修复、测试。
6. `feat: 实现下载任务、进度通知与文件发送` — download_manager/sender、`dir/file`、`pan_download_dir/pan_download_all`、清理、测试。
7. `feat: 完善帮助、清理任务与项目文档` — 完整 help、README、`docs/ADAPTER.md`、`docs/PROTOCOL.md`、端到端假 ctx 测试、PLAN 完结。

每个提交只含对应功能文件；批准后第一步先把本计划原样写入 `docs/PLAN.md`，此后每次提交更新其中的进度表。不推送远端，不修改 MaiBot 主程序。

## 14. 风险、边界与假设

- 本环境无法连真实 NapCat/SnowLuma 与百度账号：文件发送/扫码登录只能做源码级验证与假 ctx 测试，真实链路列入用户侧手工验收；失败必须回退且不虚报。
- 共享路径假设：MaiBot、适配器、NapCat/SnowLuma 同机且 `staging_dir` 可读；跨机/权限不足时自动降级链接，文档给出配置。
- Base64 10 MiB 为宿主 IPC 硬上限，阈值 8 MiB 留余量；超限走本地路径。
- 百度接口可能风控/改版：页面兜底、清晰中文错误、超时/重试可配置；不绕过验证码。
- 镜像可能失效：按序回退直连，最终支持手动 `baidu_pcs_go_path`；校验不通过拒绝安装。
- 自家分享不可转存是百度限制：改为直接下载原路径（需账号可访问该路径）。
- 许可证沿用仓库 `GPL-3.0`；manifest 与 README 一致。
- 不实现：QQ 官方适配器文件发送、云端离线下载/秒传、多账号轮换、WebUI 独立面板、打包 BaiduPCS-Go 二进制、DeepSeek 搜索相关能力。

## 15. 决策记录

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-10-17 | 文件发送统一用 `send.custom("file")`，不直连适配器 API | 统一适配器已按画像分派，NapCat/SnowLuma 一条代码路径；避免绑定适配器内部 API |
| 2026-10-17 | 管理员/白名单/镜像/黑名单均用逗号分隔字符串 | 指南未提供 `list[...]` 字段示例，规避 WebUI 列表编辑兼容性风险 |
| 2026-10-17 | 白名单文件放 `data_dir/state/whitelist.json`，管理员放 config.toml | 白名单运行期变化频繁，避免频繁改写宿主管辖的 config.toml；管理员是部署级配置 |
| 2026-10-17 | 默认白名单启用，且未配置管理员时无人可下载 | 用户确认；避免默认对所有群成员开放下载 |
| 2026-10-17 | 默认发送模式 `auto`（≤8 MiB base64，大文件共享路径，失败回退链接） | 兼顾 10 MiB IPC 硬上限与 NapCat/SnowLuma 共享目录能力 |
