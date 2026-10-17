# 百度网盘协议与 BaiduPCS-Go 集成参考

面向维护者：记录本插件使用的百度 Web 接口、错误码、登录流程与 BaiduPCS-Go CLI 约定。协议来自原插件 `astrbot_plugin_baidu_pan/main.py` 的实测行为，并已在移植时逐项对齐。

## 1. 分享接口

所有请求带浏览器 UA、`Referer: https://pan.baidu.com/disk/main`、Cookie（来自 BaiduPCS-Go 登录态）；超时 15–30s。

### 1.1 shorturlinfo

```
GET https://pan.baidu.com/api/shorturlinfo
  ?appid=250528&shorturl=<surl，含前导 1>&root=1&referer=<disk/main>
```

- 成功：顶层 `shareid`、`uk`、`title`。
- `errno == -9`：需要提取码；若同时缺 `shareid/uk`，走分享页兜底。
- 空 `shareid/uk`：分享无效或需要兜底。

### 1.2 gettemplatevariable（bdstoken）

```
GET https://pan.baidu.com/api/gettemplatevariable
  ?app_id=250528&web=1&clienttype=0&fields=["bdstoken"]
```

- 解析 `result.bdstoken`；失败时返回空串（后续需要 token 的操作会给出明确错误）。

### 1.3 share/verify（提取码）

```
POST https://pan.baidu.com/share/verify
  ?shareid=<id>&time=<毫秒>&clienttype=1&uk=<uk>
Form: pwd=<提取码>&vcode=null&vcode_str=null&bdstoken=<token>
Referer: https://pan.baidu.com/s/<surl>
```

- `errno == 0` 通过；`-9` 提取码错误；其他展示 `show_msg`。

### 1.4 share/list（目录）

```
GET https://pan.baidu.com/share/list
  ?bdstoken=<token>&root=1&web=5&app_id=250528
  &shorturl=<surl 去掉前导 1>&channel=chunlei&clienttype=0
子目录追加：&dir=<父列表返回的 path>&root=0&num=100
```

- 条目字段：`server_filename`、`isdir`（`1`/`0` 或 bool）、`fs_id`、`size`、`path`。
- `errno == -9`：需要提取码；其他错误展示 `show_msg`。

### 1.5 share/transfer（转存）

```
POST https://pan.baidu.com/share/transfer
  ?app_id=250528&channel=chunlei&clienttype=0&web=1
  &bdstoken=<token>&shareid=<id>&from=<uk>
Form: fsidlist=[123,456]&path=<目标目录>
```

转存返回的 errno 处理：

| errno | 含义 | 本插件行为 |
|---|---|---|
| `0` | 成功 | 从 `extra.list[].to` 提取文件名 |
| `4` | 已转存 | 视为成功 |
| `2` | 自己的分享 | 返回 `own_share=True`，改为直接下载原路径 |
| `12` | 冲突/空间不足 | 读 `info[].errno`：全为 `-30/0` 视为同名已存在；含 `-32` 或 `show_msg` 含“空间不足”报空间错误；否则报冲突 |
| 其他 | 失败 | 展示 `show_msg` / `err_msg` |

### 1.6 分享页兜底

`GET https://pan.baidu.com/s/<surl>`，最多 2 次（间隔 1s），正则抓取 `shareid`、`share_uk|uk`、`bdstoken`。
仅在网络异常或 JSON 解析失败时启用；接口显式 errno 不触发兜底。

## 2. 扫码登录（Passport）

1. `GET https://passport.baidu.com/v2/api/getqrcode?lp=pc&qrloginfrom=pc`
   - 返回 `sign` 与 `imgurl`（可能不带 scheme，需补 `https://`），下载 PNG 后用 `ctx.send.image(base64)` 显示。
2. 轮询 `GET https://passport.baidu.com/channel/unicast`
   - 参数：`channel_id=<sign>&gid=<uuid4 大写>&tpl=mm&_sdkFrom=1&apiver=v3&tt=<ms>&callback=`
   - 响应可能是 JSONP；解析 `channel_v`（JSON 字符串）中的 `status`/`v`：
     - `0` 等待；`1` 已扫码；带 `v` 且状态为 1/2/3 → 视为已确认；其他为失效。
3. `GET https://passport.baidu.com/v3/login/main/qrbdusslogin`
   - 参数：`bduss=<v>&u=&loginVersion=v4&qrcode=1&tpl=mm&apiver=v3`，不跟随跳转。
4. 访问 `https://pan.baidu.com/disk/main` 收齐 Cookie，要求包含 `BDUSS`；把完整 Cookie 串交给 `login -cookies=`。

轮询 60 次 × 3s（约 3 分钟）；超时或失效给出明确提示。

## 3. BaiduPCS-Go CLI 约定

安装版本：`v4.0.2`。所有调用通过环境变量 `BAIDUPCS_GO_CONFIG_DIR=<插件 data 目录>/pcs-config` 隔离登录态。

| 用途 | 命令 |
|---|---|
| 登录（BDUSS） | `login -bduss=<BDUSS> [-stoken=<STOKEN>]` |
| 登录（Cookie） | `login -cookies=<完整 Cookie>` |
| 登录（账密） | `login -username=<账号> -password=<密码>`；失败回退 stdin 交互 |
| 退出 | `logout -y` |
| 查询账号 | `who`（输出 `当前帐号 uid: N, 用户名: ...`；未登录 uid 为 0） |
| 列目录 | `ls -l <目录>` |
| 下载 | `download --ow --saveto <目录> <云端路径...>` |
| 删除 | `rm <云端路径>` |

注意：

- 标志必须放在位置参数之前；
- 退出码多数情况下不可靠（BaiduPCS-Go 丢弃了命令错误），下载成功以输出含“下载结束”、不含“以下文件下载失败”且目标文件存在综合判断；
- 输出为 UTF-8，进度行以 `\r` 刷新（`[0] ↓ 已下载/总量 速度/s ...`）；
- Windows 需要 `CREATE_NO_WINDOW`，避免弹窗。

## 4. 安装镜像与校验

- GitHub 资产地址：`https://github.com/qjfoidnh/BaiduPCS-Go/releases/download/{version}/BaiduPCS-Go-{version}-{platform}.zip`
- 镜像前缀拼接方式：`<prefix><完整 GitHub URL>`；默认 `https://gh-proxy.com/`、`https://git.yylx.win/`，最后直连 GitHub。
- v4.0.2 内置校验（GitHub Release API digest）：

| 资产 | 大小 | SHA256 |
|---|---|---|
| `windows-x64` | 5,711,812 | `ce72b3155a710b7c4a2b15611c3aebd11a057d7cccf0529e7703bdde04f0aa30` |
| `windows-arm64` | 5,067,910 | `576f7a7ab6df05996089edf766793c6bd083f19dc109fd9cf2d29b70c943191d` |
| `linux-amd64` | 5,527,431 | `b5f51388b510433668ca22fa5a1cb8840fb4d9725c5edc830554645010a62339` |
| `linux-arm64` | 4,959,481 | `d3568749f6475f9409b2d395466fa495133ca97f0582dbb15649a7074ed0b7fc` |
| `darwin-osx-amd64` | 5,625,395 | `0f002388aa944f18eeb0e2285f35463429070c78459c32b34210d59fd17be58c` |
| `darwin-osx-arm64` | 5,159,924 | `1d9b6f9f05c264a1345c198d12d612837838cea13640d3a10d1bed077068fef6` |

校验流程：逐源下载 → 校验 ZIP 头 → 比对大小 → 比对 SHA256 → 解压取 `BaiduPCS-Go(.exe)` → 原子替换 → `--version` 复验。
镜像实测（2026-10-17）：两个默认镜像均返回真实 ZIP；已失效的 `www.now61.cn` 不再使用。

## 5. 参考实现位置

原插件 `D:\TemporaryFiles\astrbot_plugin_baidu_pan-master\main.py` 中可对照的关键函数：

- `_build_pan_session`、`_share_api_preflight`、`_transfer_via_api`：HTTP 会话与分享/转存；
- `_list_share_tree_api`、`_ensure_transferred_sync`：目录树与精确项匹配；
- `download_from_cloud`、`_get_local_file_size`：下载与进度/文件发现；
- `login_bduss_bpcs`、`login_cookies_bpcs`、`login_bpcs`、`logout_bpcs`：登录；
- `_download_cn_exe`、`_download_github_exe`：旧版安装逻辑（本移植改为镜像链+固定哈希）。
