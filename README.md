# Any Router 多账号自动签到

[![GitHub Actions](https://github.com/millylee/anyrouter-check-in/workflows/PR%20Quality%20Checks/badge.svg)](https://github.com/millylee/anyrouter-check-in/actions)
[![codecov](https://codecov.io/gh/millylee/anyrouter-check-in/branch/main/graph/badge.svg)](https://codecov.io/gh/millylee/anyrouter-check-in)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/millylee/anyrouter-check-in/main.svg)](https://results.pre-commit.ci/latest/github/millylee/anyrouter-check-in/main)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/github/license/millylee/anyrouter-check-in)](LICENSE)

多账号自动签到，支持 Any Router、Agent Router 和 TokenFuture 三个平台。

推荐搭配使用[Auo](https://github.com/millylee/auo)，支持任意 Claude Code Token 切换的工具。

**维护开源不易，如果本项目帮助到了你，请帮忙点个 Star，谢谢!**

用于 Claude Code 中转站 Any Router 网站多账号每日签到，一次 $25，限时注册即送 100 美金，[点击这里注册](https://anyrouter.top/register?aff=gSsN)。业界良心，支持 Claude Sonnet 4.5、GPT-5-Codex、Claude Code 百万上下文（使用 `/model sonnet[1m]` 开启），`gemini-2.5-pro` 模型。

## 功能特性

- ✅ 多平台（兼容 NewAPI 与 OneAPI，含新版 NewAPI 的 Bearer + Turnstile 流程）
- ✅ 单个/多账号自动签到
- ✅ 多种机器人通知（可选）
- ✅ 绕过 WAF 限制

## 使用方法

### 1. Fork 本仓库

点击右上角的 "Fork" 按钮，将本仓库 fork 到你的账户。

### 2. 获取账号信息

对于每个需要签到的账号，你需要获取：(可借助 [在线 Secrets 配置生成器](https://millylee.github.io/anyrouter-check-in/))

1. **Cookies**: 用于 AnyRouter 身份验证
2. **AgentRouter 系统访问令牌**：用于读取余额；签到时还会重新执行 GitHub OAuth
3. **TokenFuture 邮箱密码或登录访问令牌**：用于公益福利页面的每日签到，详见下方 TokenFuture 配置

#### 获取 Cookies：

1. 打开浏览器，访问 https://anyrouter.top/
2. 登录你的账户
3. 打开开发者工具 (F12)
4. 切换到 "Application" 或 "存储" 选项卡
5. 找到 "Cookies" 选项
6. 复制所有 cookies

### 3. 设置 GitHub Environment Secret

1. 在你 fork 的仓库中，点击 "Settings" 选项卡
2. 在左侧菜单中找到 "Environments" -> "New environment"
3. 新建一个名为 `production` 的环境
4. 点击新建的 `production` 环境进入环境配置页
5. 点击 "Add environment secret" 创建 secret：
   - Name: `ANYROUTER_ACCOUNTS`
   - Value: 你的多账号配置数据

### 4. 多账号配置格式

支持单个与多个账号配置，可选 `name` 和 `provider` 字段：

```json
[
  {
    "name": "我的主账号",
    "cookies": {
      "acw_sc__v2": "...",
      "acw_tc": "...",
      "cdn_sec_tc": "...",
      "session": "..."
    }
  },
  {
    "name": "备用账号",
    "provider": "agentrouter",
    "access_token": "AgentRouter 系统访问令牌"
  },
  {
    "name": "TokenFuture",
    "provider": "tokenfuture",
    "email": "你的注册邮箱",
    "password": "你的登录密码"
  }
]
```

**字段说明**：

- `cookies`：AnyRouter 的 Cookie 字典或 Cookie 字符串；AnyRouter 不需要 `api_user`
- `email` + `password`：AnyRouter 可通过邮箱或用户名登录；TokenFuture 使用注册邮箱登录
- `access_token`：AgentRouter 的系统访问令牌或 TokenFuture 的登录 JWT，放在 GitHub Secret 中
- `GH_SESSION_STATE`：独立的可选 Environment Secret；由本地工具导出已完成 2FA 的 GitHub 会话，按账号 `name` 匹配
- `github_username` + `github_password`：可选；没有时复用持久化浏览器 profile 中的 GitHub 登录态
- `provider` (可选)：指定使用的服务商，默认为 `anyrouter`
- `name` (可选)：自定义账号显示名称，用于通知和日志中标识账号

**默认值说明**：

- 如果未提供 `provider` 字段，默认使用 `anyrouter`（向后兼容）
- 如果未提供 `name` 字段，会使用 `Account 1`、`Account 2` 等默认名称
- `anyrouter`、`agentrouter` 和 `tokenfuture` 有内置配置；其它 provider 会被忽略

### 4.1 AgentRouter 登录签到

AgentRouter 没有独立的签到接口，网页签到的实际流程是：先退出当前会话，再使用 GitHub OAuth 登录。脚本会请求退出接口、清理 AgentRouter 的 Cookie 和浏览器存储，保留 GitHub 的登录态，然后执行这套流程。当前页和 OAuth 弹窗均受支持，登录后会通过 `/api/user/self` 验证会话。

系统访问令牌只用于请求 `/api/user/self`，不能替代 GitHub 登录。请放在 `ANYROUTER_ACCOUNTS` Secret 中，不要提交到仓库。

首次使用时，在本地设置 `CHECKIN_HEADLESS=false`，必要时增大 `CHECKIN_WAIT_TIMEOUT_MS`，在打开的浏览器中完成 GitHub 登录、授权和二次验证，之后可复用同一个账号名称对应的 `.browser_profiles/agentrouter/` profile。也可配置 `github_username` 和 `github_password` 自动填写登录表单；遇到二次验证仍需人工完成，超时会明确报告失败。

GitHub Actions 的运行环境不会自动获得本地 profile。启用 2FA 时，推荐使用下方本地工具完成一次人工认证，再导入 `GH_SESSION_STATE`。仅配置系统访问令牌不足以完成签到。Actions 使用临时浏览器，不保存或恢复包含登录凭据的 profile 缓存；本地正常签到仍可复用持久化 profile。

`github_username` 和 `github_password` 放在 `ANYROUTER_ACCOUNTS` 的 AgentRouter 对象内，不是单独的 Actions Secret。已有可用的 GitHub 登录态时可以省略。遇到登录表单缺少凭据、GitHub 登录错误或二次验证时，脚本会给出针对性提示；调试模式会保存 OAuth 新窗口的截图，日志中的页面地址不包含查询参数和片段。截图可能包含账号信息，请勿公开分享。

若日志出现 `Access Verification` / `Please slide to verify`，说明请求在到达 GitHub 登录前就被站点的滑块验证拦截，并不是缺少用户 ID 或系统访问令牌。Actions 的 Xvfb 只是虚拟显示器，无法人工完成验证；脚本会明确报告该问题，不再重复等待登录页。可尝试配置 `PROXY_SUBSCRIPTION_URL` 使用可访问站点的代理出口，但代理不能保证消除验证。本地可用 `CHECKIN_HEADLESS=false` 在浏览器中人工完成验证。

#### 保留 2FA：本地认证后导入 Actions

1. 在本地桌面环境安装项目依赖，选择 CloakBrowser 或本机 Firefox，安装 GitHub CLI（`gh`），通过 `gh auth login` 登录有权管理该仓库 Secrets 的账号。GitHub CLI 的认证用于上传 Secret，和浏览器中登录 AgentRouter 的 GitHub 账号是两件事。
2. 确认仓库已有 `production` Environment，`ANYROUTER_ACCOUNTS` 中 AgentRouter 的 `name` 是 `AgentRouter`。若使用其他名称，修改下方的 `--account-name`。
3. 在项目目录运行：

```bash
uv sync --frozen
uv run python -m cloakbrowser install
uv run python scripts/github_session.py --account-name AgentRouter --repo rzhli/anyrouter-check-in
```

**本机已有 Firefox 时，可以改用以下命令，不需要下载 CloakBrowser 的浏览器二进制：**

```bash
uv run --extra firefox python scripts/github_session.py --browser firefox --account-name AgentRouter --repo rzhli/anyrouter-check-in
```

`firefox` 是可选依赖，仅为本地导出工具安装 Selenium。Firefox 必须在 `PATH` 中；Selenium Manager 首次运行会下载较小的 `geckodriver`，若该下载也受网络限制，请自行安装 `geckodriver` 并加入 `PATH`。工具启动独立临时 Firefox profile，不读取或修改你日常 Firefox 的已登录会话，因此仍需在新窗口完成一次登录和 2FA，结束后关闭临时浏览器。Actions 的签到浏览器仍使用 CloakBrowser，不受此选项影响。

4. 在新打开的本地浏览器中，用绑定 AgentRouter 的 GitHub 账号完成用户名、密码、六位验证码和必要的设备验证。完成后回到终端按 Enter。工具会访问 GitHub 的个人设置页确认已登录，只导出 `github.com` Cookie，并通过 `gh` 的标准 Secret 加密上传流程更新 `production` 的 `GH_SESSION_STATE`。密码、验证码、2FA 密钥和浏览器 profile 均不导出；导出的 JSON 通过标准输入提交，不生成会话文件，也不出现在命令参数或终端输出中。
5. 重新运行 Actions。脚本按账号名称导入会话，退出时仅清理 AgentRouter 登录态，保留 GitHub Cookie；因此通常可直接完成 OAuth，无需在 Actions 中输入六位验证码。`ANYROUTER_ACCOUNTS` 中可以省略 `github_username` 和 `github_password`。

本地工具默认直连；需要代理时先设置 `CHECKIN_PROXY_URL`，再添加 `--use-proxy`。Firefox 支持明确指定端口、不包含用户名密码的 HTTP 或 SOCKS5 代理，例如 `http://127.0.0.1:7890`；代理选项不控制 Selenium Manager 的驱动下载。`--environment` 可覆盖默认的 `production`。多个 AgentRouter 账号可重复传入 `--account-name`，每个账号都会打开独立浏览器供你登录；一次上传替换整个 Secret，因此应同时导出所有需要的账号。

使用 `--repo` 时，工具会在打开登录浏览器前只读预检目标 Environment 的 Secret 公钥接口。未登录 `gh`、仓库/环境名称错误或接口不可访问时先停止，避免白做一次 2FA；预检通过不保证后续写入一定成功。浏览器中的 GitHub 登录不能代替 GitHub CLI 认证。失败提示仅按认证、权限、目标不存在、网络等类别给出建议，不输出可能包含会话的原始 CLI 响应。

预检和上传遇到网络超时、连接失败或 GitHub 5xx 时，最多额外重试两次；明确的认证、权限、目标不存在和限流错误不会自动重试。浏览器的 `--use-proxy` 不会给 `gh` 配置代理，CLI 网络需要在终端单独配置。

若不能直接上传，可使用 `--output .session_exports/github-session.json` 代替 `--repo`，再将文件全部内容粘贴到仓库 Settings → Environments → `production` → Environment secrets 的 `GH_SESSION_STATE` Secret。这条路径不需要 `gh` 认证。文件权限为 `0600`，目录已加入 `.gitignore`，已存在的文件不会被覆盖。它是明文登录凭证，导入后请删除，不要发送到聊天、提交到仓库或上传为 artifact。默认直接上传失败时不会自动保存会话；此时需要重新导出。

Secret 名称必须是 `GH_SESSION_STATE`，不要以 GitHub 保留的 `GITHUB_` 前缀命名。已有导出文件的 JSON 格式不变，无需为更名重新登录或导出。

**会话不是永久有效，也不能绕过 GitHub 的安全策略。** 会话过期、退出、撤销或换出口后被要求重新验证时，需要再次运行工具更新 Secret；不要关闭 2FA，也不要把六位验证码或 2FA 密钥配置到 Actions。会话与密码一样敏感，应仅允许可信维护者修改使用该 Secret 的 workflow。若旧版本已生成 `browser-profiles-*` 缓存，请在 Actions → Caches 中删除这些缓存；移除缓存步骤不会自动删除历史缓存。

### 4.2 AnyRouter Cookie

AnyRouter 账号不需要 `user_id` 或 `api_user`。Cookie 中填写 `acw_sc__v2`、`acw_tc`、`cdn_sec_tc` 和 `session` 四项即可。

每次运行会重新获取 WAF Cookie，以浏览器获取的新值覆盖配置中的同名旧值，同时保留账号的 `session`。HTTP 200 不代表签到成功；如果响应是 HTML 验证页或登录重定向而不是 JSON，脚本会报告响应类型，需检查站点验证、代理出口及 Cookie 有效性。

![获取 cookies](./assets/request-session.png)

### 4.3 TokenFuture 公益福利签到

签到页面：https://tokenfuture.si/custom/public-benefits 。在 `ANYROUTER_ACCOUNTS` 数组中追加：

```json
{
  "name": "TokenFuture",
  "provider": "tokenfuture",
  "email": "你的注册邮箱",
  "password": "你的登录密码"
}
```

邮箱密码方式每次通过浏览器登录，等待站点的 Turnstile 验证完成后自动获取新的登录令牌。也可以用 `"access_token": "你的登录 JWT"` 替代 `email` 和 `password`：登录网站后，在浏览器开发者工具 → Application / 存储 → Local Storage → `https://tokenfuture.si` 中复制 `auth_token` 的值。该令牌是网页登录凭证，**不是用于模型调用的 `sk-...` API Key**；令牌过期后需要重新获取。两种方式同时配置时优先邮箱密码；Cookie 和 GitHub 登录不适用于 TokenFuture。

TokenFuture 使用 Sub2API：先通过 `/api/v1/auth/me` 读取余额，再查询 `/api/v1/user/checkin` 的 `enabled` / `checked_in` 状态；未签到时向同一接口 POST 空 JSON。已经签到会直接成功跳过，活动尚未开放、登录失效或返回格式异常会明确报告失败。余额和 `/api/v1/usage/dashboard/stats` 的累计实际消耗直接以美元返回，不按 NewAPI 的 quota 单位换算。

邮箱登录时若验证超时，可在本地设置 `CHECKIN_HEADLESS=false` 完成验证；GitHub Actions 中的无人值守验证能否完成取决于站点。启用登录二次验证时请使用已完成验证的 `auth_token`。账号配置仍放在 `production` 的 `ANYROUTER_ACCOUNTS` Secret 中，无需新增 workflow 或 `PROVIDERS` 配置。


### 5. 启用 GitHub Actions

1. 在你的仓库中，点击 "Actions" 选项卡
2. 如果提示启用 Actions，请点击启用
3. 找到 "AnyRouter 自动签到" workflow
4. 点击 "Enable workflow"

### 6. 测试运行

你可以手动触发一次签到来测试：

1. 在 "Actions" 选项卡中，点击 "AnyRouter 自动签到"
2. 点击 "Run workflow" 按钮
3. 确认运行

![运行结果](./assets/check-in.png)

## 执行时间

- 脚本每 6 小时执行一次（1. action 无法准确触发，基本延时 1~1.5h；2. 目前观测到 anyrouter 的签到是每 24h 而不是零点就可签到）
- 你也可以随时手动触发签到

## 注意事项

- 请确保 AnyRouter 的四项 Cookie、AgentRouter 的系统访问令牌和 GitHub 登录态、TokenFuture 的登录凭据有效
- 可以在 Actions 页面查看详细的运行日志
- 支持部分账号失败，只要有账号成功签到，整个任务就不会失败
- 报 401 错误，请重新获取 cookies，理论 1 个月失效，但有 Bug，详见 [#6](https://github.com/millylee/anyrouter-check-in/issues/6)
- 请求 200，但出现 Error 1040（08004）：Too many connections，官方数据库问题，目前已修复，但遇到几次了，详见 [#7](https://github.com/millylee/anyrouter-check-in/issues/7)

## 配置示例

### 基础配置（向后兼容）

AnyRouter 不需要 `api_user`，直接填写四项 Cookie：

```json
[
  {
    "cookies": {
      "acw_sc__v2": "...",
      "acw_tc": "...",
      "cdn_sec_tc": "...",
      "session": "..."
    }
  }
]
```

### 多服务商配置

如果你需要同时使用多个服务商：

```json
[
  {
    "name": "AnyRouter 主账号",
    "provider": "anyrouter",
    "cookies": {
      "acw_sc__v2": "...",
      "acw_tc": "...",
      "cdn_sec_tc": "...",
      "session": "..."
    }
  },
  {
    "name": "AgentRouter 备用",
    "provider": "agentrouter",
    "access_token": "AgentRouter 系统访问令牌"
  },
  {
    "name": "TokenFuture",
    "provider": "tokenfuture",
    "access_token": "TokenFuture Local Storage 中的 auth_token"
  }
]
```

## Provider 配置

脚本接受内置的 `anyrouter`、`agentrouter` 和 `tokenfuture`。`PROVIDERS` 用于覆盖这些 provider 的设置，其他站点会被忽略。

可按需覆盖域名、代理和 GitHub OAuth 开关等设置：

```json
{
  "agentrouter": {
    "domain": "https://agentrouter.org",
    "use_proxy": true,
    "github_oauth": true
  }
}
```

### 在 GitHub Actions 中配置

1. 进入你的仓库 Settings -> Environments -> production
2. 添加新的 secret：
   - Name: `PROVIDERS`
   - Value: 你的 provider 配置（JSON 格式）

**字段说明**：

- `domain` (可选)：覆盖内置 provider 的域名
- `login_path` (可选)：登录页面路径，默认为 `/login`（仅在 `bypass_method` 为 `"waf_cookies"` 时使用）
- `sign_in_path` (可选)：签到 API 路径，默认为 `/api/user/sign_in`
- `user_info_path` (可选)：用户信息 API 路径，默认为 `/api/user/self`
- `bypass_method` (可选)：WAF 绕过方法
  - `"waf_cookies"`：使用 CloakBrowser 打开浏览器获取 WAF cookies 后再执行签到
  - 不设置或 `null`：直接使用用户 cookies 执行签到（适合无 WAF 保护的网站）
- `waf_cookie_names` (可选)：绕过 WAF 所需 cookie 的名称列表，`bypass_method` 为 `waf_cookies` 时必须设置

- `github_oauth` (可选)：AgentRouter 使用 GitHub OAuth 触发签到，默认 `true`

**内置配置说明**：

- `anyrouter`：
  - `bypass_method: "waf_cookies"`（需要先获取 WAF cookies，然后执行签到）
  - `sign_in_path: "/api/user/sign_in"`
- `agentrouter`：
  - `bypass_method: "waf_cookies"`（需要获取 `acw_tc`）
  - `sign_in_path: null`（无独立签到 API，通过重新登录触发签到）
  - `use_proxy: true`
  - `github_oauth: true`（先登出，再用 GitHub OAuth 登录触发签到）

**重要提示**：

- `PROVIDERS` 是可选的，不配置则使用内置的 `anyrouter`、`agentrouter`、`tokenfuture`
- 自定义的 provider 配置会覆盖同名的默认配置

## 代理配置（可选）

内置的 `agentrouter` 默认 `use_proxy: true`。如果你的运行环境访问该平台不稳定，可以在 GitHub Actions 中配置 mihomo 订阅代理。

在仓库 Settings -> Environments -> production -> Environment secrets 中添加：

- `PROXY_SUBSCRIPTION_URL`：Clash/Mihomo 订阅链接。设置后，workflow 会运行 `scripts/setup_mihomo_proxy.sh`，启动本地代理并写入 `CHECKIN_PROXY_URL`。

本地运行时也可以直接使用已有代理：

```bash
CHECKIN_PROXY_URL=http://127.0.0.1:7890
PROVIDERS={"agentrouter":{"use_proxy":true}}
```

如果使用订阅脚本，默认会用 `https://www.google.com/generate_204` 测试代理连通性；也可以通过 `PROXY_TEST_URL` 覆盖。

## 开启通知

脚本支持多种通知方式，可以通过配置以下环境变量开启，如果 `webhook` 有要求安全设置，例如钉钉，可以在新建机器人时选择自定义关键词，填写 `AnyRouter`。

### 邮箱通知(STMP)

- `EMAIL_USER`: 发件人邮箱地址/STMP 登录地址
- `EMAIL_PASS`: 发件人邮箱密码/授权码
- `EMAIL_SENDER`: 邮件显示的发件人地址(可选，默认: EMAIL_USER)
- `CUSTOM_SMTP_SERVER`: 自定义发件人 SMTP 服务器(可选)
- `EMAIL_TO`: 收件人邮箱地址

### 钉钉机器人

- `DINGDING_WEBHOOK`: 钉钉机器人的 Webhook 地址

### 飞书机器人

- `FEISHU_WEBHOOK`: 飞书机器人的 Webhook 地址

### 企业微信机器人

- `WEIXIN_WEBHOOK`: 企业微信机器人的 Webhook 地址

### PushPlus 推送

- `PUSHPLUS_TOKEN`: PushPlus 的 Token

### Server 酱

- `SERVERPUSHKEY`: Server 酱的 SendKey

### Telegram Bot

- `TELEGRAM_BOT_TOKEN`: Telegram Bot 的 Token
- `TELEGRAM_CHAT_ID`: Telegram Chat ID

### Gotify 推送

- `GOTIFY_URL`: Gotify 服务的 URL 地址（例如: https://your-gotify-server/message）
- `GOTIFY_TOKEN`: Gotify 应用的访问令牌
- `GOTIFY_PRIORITY`: Gotify 消息优先级 (1-10, 默认为 9)

### Bark 推送

- `BARK_KEY`: Bark 应用的 Key（APP 打开时即可看到）
- `BARK_SERVER`: 自建 Bark 服务器地址 (可选，默认: https://api.day.app)

配置步骤：

1. 在仓库的 Settings -> Environments -> production -> Environment secrets 中添加上述环境变量
2. 每个通知方式都是独立的，可以只配置你需要的推送方式
3. 如果某个通知方式配置不正确或未配置，脚本会自动跳过该通知方式

## 故障排除

如果签到失败，请检查：

1. 账号配置格式是否正确
2. cookies 是否过期
3. AgentRouter 的 GitHub profile 是否已登录
4. 网站是否更改了签到接口
5. 查看 Actions 运行日志获取详细错误信息

## 本地开发环境设置

如果你需要在本地测试或开发，请按照以下步骤设置：

```bash
# 安装所有依赖
uv sync --dev

# 安装 CloakBrowser 浏览器
uv run python -m cloakbrowser install
# 如需使用本地浏览器，可设置 CLOAKBROWSER_BINARY_PATH=/path/to/browser

# 创建 .env 文件并配置（注意：JSON 必须是单行格式）
# 示例：
# ANYROUTER_ACCOUNTS=[{"name":"账号1","email":"your@email.com","password":"your_password"}]
# PROVIDERS={"agentrouter":{"domain":"https://agentrouter.org"}}
# PROXY_SUBSCRIPTION_URL=https://example.com/sub?token=xxx
# CHECKIN_PROXY_URL=http://127.0.0.1:7890

# 运行签到脚本
uv run checkin.py
```

## 测试

```bash
uv sync --dev

# 浏览器相关测试或本地登录可安装 CloakBrowser，或设置 CLOAKBROWSER_BINARY_PATH 指向本地浏览器
uv run python -m cloakbrowser install

# 运行测试
uv run pytest tests/

# 查看测试覆盖率
uv run pytest tests/ --cov=. --cov-report=html
```

## 贡献指南

欢迎贡献代码！在提交 Pull Request 之前，请阅读[贡献指南](CONTRIBUTING.md)。

### 代码质量

本项目使用以下工具确保代码质量：

- **Ruff**: 代码风格检查和格式化
- **MyPy**: 静态类型检查
- **Bandit**: 安全漏洞扫描
- **Pytest**: 自动化测试
- **pre-commit**: Git 提交前自动检查

所有 Pull Request 会自动运行以下检查：

- ✅ 代码风格检查（Ruff Lint & Format）
- ✅ 类型检查（MyPy）
- ✅ 安全扫描（Bandit）
- ✅ 测试运行（Pytest）
- ✅ 测试覆盖率报告（Codecov）

### 本地开发

```bash
# 安装开发依赖
uv sync --dev

# 安装 pre-commit 钩子
uv run pre-commit install

# 运行代码检查
uv run ruff check .
uv run ruff format .
uv run mypy .
uv run bandit -r . -c pyproject.toml

# 运行测试
uv run pytest tests/ --cov=.
```

## 免责声明

本脚本仅用于学习和研究目的，使用前请确保遵守相关网站的使用条款.
