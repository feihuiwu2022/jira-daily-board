---
name: jira-daily-board
description: "Jira 问题日清看板：从内网 Jira 拉取数据，同步到飞书多维表格，仪表盘自动刷新。支持浏览器登录/PAT/工号密码三种授权方式。当用户提到 Jira 看板、Jira 日清看板、Jira 问题看板、Jira 飞书看板、Jira 多维表格看板、Jira Bug看板、Jira 缺陷看板、Jira 问题统计、Jira 闭环统计、Jira 超时预警、Jira 未闭环时使用。"
---

# Jira 问题日清看板（飞书多维表格版）

从内网 Jira 拉取问题 → 计算闭环阶段与时效 → 同步到飞书多维表格（问题明细表 + 日清快照表），仪表盘基于这两张表展示。

所有脚本在本 SKILL 目录下运行；配置保存在 `~/.jira_board_config.json`。

## 首次配置（交互式引导）

**取数原则：Jira 数据一律由本机 Python 按 JQL 调 REST API 拉取（`daily_board.py`），数据直接写盘。不要用 browser evaluate 拉取或搬运问题数据，也不要自己写 curl / 临时脚本分批拉取——几千条问题有十几 MB，经过对话会卡住。浏览器只用于登录和生成 Token。**

先运行 `python setup_wizard.py --check`：
- 退出码 0（已有配置）→ 把输出的配置摘要给用户看，问一句"沿用这份配置直接更新看板吗？回车 = 是；说『重新配置』则重新开始"。用户确认就进入「日常运行」；要重新配置则执行 `python setup_wizard.py --reset` 后从 ① 开始。
- 否则按下面步骤**一步一问**，每步等用户回答后再进入下一步；能自动判断的（如 JQL 是否有效）不要再让用户确认。

### 第 0 步：确认飞书授权

同步依赖 lark-cli 的 user 身份。如果助手还没有飞书授权，先引导用户完成飞书授权，再继续。

### ① 选择 Jira 授权方式

代码里不内置任何 Jira 地址。配置里还没有 `jira_base` 时，先在同一条消息里问一句："你的 Jira 地址是？（例如 https://jira.example.com，也可以直接发一个 Jira 筛选链接）"。
用户发来的若是筛选链接，`--jql-url` 会同时提取 Jira 地址和 JQL，第 ② 步就不用再问；只给了地址则用 `--jira-base` 传入。

```
助手: 欢迎使用 Jira 问题日清看板！首次使用需要配置，我们一步步来。
     ① 请选择 Jira 授权方式：
        A. 浏览器登录（推荐，密码不经过对话）
        B. Personal Access Token
        C. 工号 + 密码
```

**A. 浏览器登录（自动生成 Token）**
1. `browser navigate` 打开 `{jira_base}/login.jsp`，提示"请在浏览器中登录，完成后告诉我"。
2. 用户说登录好了 → 在该页面 `browser evaluate` 生成一个 Personal Access Token（只返回一个 Token，数据量很小）：
   ```js
   fetch('/rest/pat/latest/tokens',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','X-Atlassian-Token':'no-check'},body:JSON.stringify({name:'jira-daily-board',expirationDuration:90})}).then(async r=>({status:r.status,body:await r.json().catch(()=>null)}))
   ```
   - `status` 为 200/201 且 `body.rawToken` 有值 → 成功。记下 Token，**不要在回复里显示它**。
   - `status` 401/403 → 还没登录成功，提示"请在浏览器中重新登录后告诉我"，重复本步。
   - `status` 404 或 Token 生成被禁用 → 这台 Jira 不支持 PAT，改用 C（工号 + 密码）。
   - 管理员限制了有效期时，把 `expirationDuration` 改小（单位：天）重试。
3. 进入 ②。校验 JQL 时一起传：`setup_wizard.py --auth browser --pat-token {rawToken} --jql-url "{链接}" --check-jql`。
   之后取数和 PAT 完全一样，由 Python 直接调 REST API。

**B. Personal Access Token**
1. 引导用户：打开 `{jira_base}/secure/ViewProfile.jspa` → 左侧 "Personal Access Tokens" → "Create Token" → 起名（如 `jira-board`）→ 复制 Token（只显示一次）并粘贴给助手。
2. 在 ② 校验 JQL 时一起传：`setup_wizard.py --auth pat --pat-token {token} --jql-url "{链接}" --check-jql`，Token 和 JQL 一次验证。
   向导会实际调用 Jira 验证；**退出码 2 / 输出 `__AUTH_FAILED__`** 表示 Token 无效或过期，提示用户重新生成或换授权方式。

**C. 工号 + 密码**
1. 先问工号，再问密码。问密码时附一句提醒（只是建议，不要直接甩终端命令）：
   ```
   助手: 请输入 Jira 密码。密码只用于本机加密保存，我不会在回复里复述。
        💡 如果介意密码出现在聊天记录里，也可以选择 A（浏览器登录），或告诉我"我自己在终端输入"。
   ```
2. 用户在对话里给出密码 → 通过标准输入传给向导（不要放在命令行参数里，也不要在任何回复里回显密码）：
   PowerShell：`'{密码}' | python setup_wizard.py --auth credentials --user {工号} --password-stdin ...`
   （单引号包住，密码里的 `'` 写成 `''`；不要用 CMD 的 `echo`，它会带上末尾空格并解析 `& ^ %` 等字符。
   也可以设置环境变量 `JIRA_PASS` 后调用，不带 `--password-stdin`。）
3. 只有当用户主动要求自己在终端输入时，才给出命令：
   `python setup_wizard.py --auth credentials --user {工号}`，按提示输入（不回显），完成后告诉助手。
- 凭证用 Windows DPAPI 加密保存到本机（需要 pywin32；没有时只是混淆，向导会提示）。
- 退出码 2 表示工号或密码错误：提示"工号或密码不正确，请重新输入密码"，回到第 1 步。
- 退出码 3 表示连不上 Jira（网络/VPN/地址问题），不是密码错，提示用户检查网络后重试。

### ② Jira 数据源

```
助手: ② 请提供 Jira 数据源：
     方式1：在 Jira 里筛选好问题，复制浏览器地址栏链接给我（推荐）
     方式2：直接输入 JQL
```

收到后**不要再问"JQL 确认没问题吗"**，直接自动校验：

- `python setup_wizard.py --jql-url "{链接}" --check-jql`（JQL 用 `--jql "{JQL}"`；授权参数还没保存的话一并带上）
  - `__JQL_OK__ <条数>` → 校验通过
  - `__JQL_ERROR__ …`（退出码 5）→ 告诉用户 JQL 有误并附错误原因，请重新提供，停在本步
  - 退出码 2 / 3 → 按授权失败 / 连不上 Jira 处理

校验通过后，**在同一条回复里**直接给出 ③，不要等用户确认。

### ③ 其余选项（一次问完，直接回车 = 全部默认）

```
助手: ✅ JQL 有效：项目 ABC，指定提单人，近 9 周命中 1234 条。

     ③ 其余选项，直接回车 = 全部默认：
        1. 飞书存放位置：「我的空间/jira-daily-board」（可发飞书文件夹链接或路径，如 项目/产品A/看板）
        2. 邮件收件人：不发送（多个邮箱用逗号分隔）
        3. 每天定时生成：17:00
        4. 看板标题：问题日清看板
        5. 回溯周数：9 周（可选 2/4/9/13/26/全部）
     要改哪项直接说，例如"2 发给 a@x.com，5 改成 13 周"。
```

- 用户回车、说"默认""全部默认""好的"等 → 全部用默认值。
- 用户只改其中几项 → 只改那几项，其余默认；不要逐项追问。
- 用户发来的是飞书文件夹链接或像路径的文字 → 当作第 1 项。
- 只有回答确实看不懂时才追问一次。

### 保存、建表并生成第一份看板

把收集到的内容一次性交给向导（没改的项不用传）：

```bash
python setup_wizard.py --jql-url "{链接}" --create-base \
  [--feishu-folder "{链接或路径}"] [--mail-to "a@x.com"] [--schedule-time "17:00"] \
  [--title "XX 项目 · 问题日清看板"] [--weeks 13]
```

`--create-base` 会创建（或复用同名的）多维表格「Jira 问题日清看板」：
- 「问题明细」表（含 问题来源 / 缺陷类型 / 问题原因大类 扩展列）和「日清快照」表；
- 视图：问题总表、超时预警明细、未闭环问题、🔴 S级问题、🟠 A级问题、⏰ 严重超时、📅 今日更新、📊 按功能模块分组、📊 按等级分组、📅 今日新增（日期筛选用相对日期，不需要每天改）；
- 仪表盘「Bug日清看板」：问题总数、超时问题数、预警问题数、未闭环数、等级分布、Jira状态分布、按周趋势、责任工程师未闭环排行、功能模块分布、时效标签分布、问题来源分布、缺陷类型分布、问题原因大类。

退出码：0 成功；1 缺必填项（按输出补问）；2 Jira 授权验证失败（配置已保存，只需重新授权）；3 连不上 Jira（检查网络/VPN）；4 飞书多维表格创建失败（输出 `__FEISHU_SETUP_FAILED__`，常见原因是飞书授权缺少云文档/多维表格权限，或文件夹没有编辑权限）。

成功后立即执行一次「日常运行」，然后用一条消息告诉用户：配置摘要、多维表格链接、今日看板数字。

**补充说明**
- 扩展列的 Jira 字段：问题来源默认取「缺陷来源」(customfield_10220)；缺陷类型、问题原因大类按 Jira 字段名自动查找。查不到时该列为空，可在配置 `extra_fields` 里指定，如 `{"缺陷类型": "customfield_12345"}`。
- 已有的多维表格缺字段、视图或仪表盘组件（例如用旧版本建的表）：`python setup_wizard.py --repair-base`，只补缺的，不改动已有内容和布局。
- `--create-base` / `--repair-base` 最后会输出一行「检查结果：问题明细视图 x/10，仪表盘组件 y/13」，把这一行原样告诉用户。
  输出 `__FEISHU_INCOMPLETE__` 时，同时把「缺少视图/组件」和 `[问题]` 那几行发给用户（这是当前 lark-cli 版本不支持某些命令导致的，需要维护者适配），不要自己改用其他方式去建。
  旧版 lark-cli 不支持的写法会自动降级：视图筛选改用旧写法，条形图 / 环形图改为柱状图 / 饼图。
- 已经有现成的多维表格、不想新建时（少见），用 `--base-url "{多维表格链接}" --detail-table-url "{问题明细表链接}" --snapshot-table-url "{日清快照表链接}"` 代替 `--create-base`，再执行一次 `--repair-base` 补齐。

其他命令：`--show` 查看配置，`--check` 检查是否完整，`--reset` 清除配置。

## 日常运行

用户说"更新看板""生成今天的看板"，或到达定时时间（助手按 `schedule_time` 创建每日定时任务）时执行。三种授权方式都一样，只需要一条命令：

```bash
python daily_board.py
```

脚本按 JQL 分页调 REST API（500 条/页，2000 多条问题约 5 次请求），再批量取评论、同步飞书，全程不需要浏览器。

- 执行这条命令时把超时设为 **15 分钟**（首次同步要写入全部问题，最慢；之后每天只重写有变化的行，通常一两分钟）。等它运行结束再读输出，不要中途中断、不要改用其他方式取数或同步。
- 输出里有各阶段用时（`[jira] 取数用时`、`[问题明细] … 用时`、`飞书同步用时`），用户嫌慢时把这几行发给维护者。
- 如果确实被超时打断：数据已经在本地，执行 `python daily_board.py --skip-fetch` 只做同步。

### 输出标记与处理

| 输出 | 含义 | 助手的处理 |
|---|---|---|
| `__AUTH_EXPIRED__` | Jira 授权失效（Token 过期/撤销，或密码已改） | 浏览器登录方式：按 ①-A 重新登录并生成 Token，`setup_wizard.py --auth browser --pat-token {新 Token}`；PAT：引导重新生成 Token 并 `setup_wizard.py --pat-token`；工号密码：重新询问密码并用 `--password-stdin` 重新保存。然后重跑 `daily_board.py` |
| `[daily] !! 同步失败` | 飞书写入失败 | 明细表已自动回滚、旧数据保留；检查飞书授权和表字段后重跑 |
| 退出码 0 | 成功 | 把 `out/看板摘要_YYYY-MM-DD.txt` 的内容和多维表格链接发给用户；配置了 `mail_to` 时按 `title` 作标题发送邮件 |

其他参数：`--check-auth` 只检查授权；`--print-jql` 打印实际 JQL；`--skip-fetch` 不重新取数、只用上次的数据重新同步。

## 飞书多维表格结构

### 问题明细表

| 字段名 | 类型 | 说明 |
|---|---|---|
| 编号 | text | `[KEY](Jira 链接)` |
| 等级 | select | S / A / B+ / B / C |
| 闭环状态 | select | 0/4 · 1/4 · 2/4 · 3/4 · 4/4 · 挂起 · 跟踪中 |
| 时效标签 | select | 正常 · 预警 · 超时 · 严重超时 |
| 停留天数 | number | 距锚点天数 |
| 责任工程师 | text | assignee 显示名 |
| 功能模块 | text | |
| 概要 | text | |
| 创建日期 | datetime | |
| 更新时间 | datetime | |
| 周分桶 | text | ISO 周号 CW## |
| 最新进展 | text | 最新评论摘要（无评论时取解决方案/根因分析） |
| Jira状态 | text | Jira 原始状态 |
| 问题来源 | text | Jira「缺陷来源」 |
| 缺陷类型 | text | Jira「缺陷类型」（按字段名自动查找） |
| 问题原因大类 | text | Jira「问题原因大类」（按字段名自动查找） |

### 日清快照表

| 字段名 | 类型 | 说明 |
|---|---|---|
| 日期 | datetime | 快照日期 |
| 问题总数 | number | |
| 今日新增 | number | |
| 今日解决 | number | 当天首次到达 3/4+（当前仍在 3/4 或 4/4） |
| 本周新增 / 本周解决 | number | 自本周一起 |
| 超时数 | number | 超时 + 严重超时 |
| 预警数 | number | |
| 未闭环数 | number | 0/4 + 1/4 + 2/4 |

### 同步策略

- 问题明细表：先写入全部新数据，成功后再删除旧数据；任一批写入失败会删除本次已写入的记录并以非 0 退出，旧数据不动。
- 日清快照表：按日期 upsert，同一天多次运行只更新不追加。

## 口径

### 闭环状态映射

| Jira 状态 | 闭环状态 |
|---|---|
| 待办 / 已分配 | 0/4 |
| 分析中 | 1/4 |
| 修复中 | 2/4 |
| 验收中 / 审核中 | 3/4 |
| 完成 | 4/4 |
| 挂起 / 申请挂起中 | 挂起 |
| 跟踪中 | 跟踪中 |

### 时效

| 闭环状态 | 锚点 | 预警 | 超时 |
|---|---|---|---|
| 0/4 | 创建日期 | > 1 天 | > 3 天（严重超时） |
| 1/4 · 2/4 · 3/4 | 进入该阶段日期 | > 7 天 | > 12 天 |
| 4/4、挂起、跟踪中 | 不计 | — | — |

阈值在 `metrics.py` 顶部常量中调整；快照表和文本摘要共用 `metrics.compute_stats`，数字保持一致。

## 文件说明

| 文件 | 用途 |
|---|---|
| `setup_wizard.py` | 首次配置向导（含 Jira 授权验证） |
| `daily_board.py` | 每日入口：取数 → 同步 → 摘要 |
| `jira_client.py` | Jira REST 授权、分页取数、评论、时间窗 JQL |
| `metrics.py` | 闭环阶段、时效、统计口径 |
| `sync_to_base.py` | 通过 lark-cli 同步到飞书多维表格 |
| `feishu_setup.py` | 自动创建文件夹、多维表格、两张表、视图和仪表盘 |
| `lark_cli.py` | lark-cli 调用封装 |
| `site_defaults.example.json` | 站点默认值模板：复制为 `site_defaults.json` 填写本单位的 Jira 地址、飞书域名、lark-cli 路径/profile |
| `config_manager.py` | 配置读写、链接解析、凭证加解密 |
| `fetch_jira.py` | 独立取数调试工具（日常流程不用） |

产物：`_work/issues.json`、`_work/comments.json`（原始数据），`out/看板摘要_YYYY-MM-DD.txt`（保留 30 份）。

## 注意事项

- `~/.jira_board_config.json` 含 PAT/加密凭证，不要外发。
- 同目录下有 `site_defaults.json` 时，其中的 Jira 地址、飞书域名、lark-cli 路径/profile 作为默认值（模板见 `site_defaults.example.json`），配置 Jira 地址那一句就不用问了。
- lark-cli 默认用 PATH 中的 `lark-cli` 和它的默认 profile。宿主程序自带 lark-cli 或使用专用 profile 时，首次配置加上 `--lark-cli "<路径>" --lark-profile "<profile>"`（只保存在本机配置文件里），或设置环境变量 `LARK_CLI` / `LARK_PROFILE`。
- PAT 按 Jira 规范用 `Bearer` 发送，不行时自动退回 `Basic pat:<token>`，向导会记住可用的那种。
