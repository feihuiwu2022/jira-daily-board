# Jira 问题日清看板（飞书多维表格版）

把内网 Jira 的 Bug 每天同步到飞书多维表格，用飞书仪表盘展示问题总数、超时/预警、未闭环、等级分布、状态分布和按周趋势。

以 AI 助手 SKILL 的形式使用：首次使用时助手按 [`SKILL.md`](SKILL.md) 一步步引导完成 Jira 授权、数据源和飞书表格配置，之后每天手动或定时更新看板。

## 工作流程

```
Jira（REST API / 浏览器登录态）
   │  按 JQL + 回溯周数分页拉取问题和最新评论
   ▼
_work/issues.json、comments.json
   │  metrics.py：闭环阶段、时效标签、每日统计
   ▼
lark-cli（飞书 user 身份）
   ├─ 问题明细表：先写新数据、成功后删旧数据，失败自动回滚
   └─ 日清快照表：按日期 upsert
   ▼
飞书仪表盘自动刷新 + out/看板摘要_YYYY-MM-DD.txt
```

## 准备

- Python 3.8+（只用标准库；Windows 上加密保存工号密码需要 `pip install pywin32`）
- lark-cli 已完成飞书授权（默认用 PATH 中的 lark-cli，也可用配置 `lark_cli` 或环境变量 `LARK_CLI` 指定）
- 飞书多维表格不用自己建：`setup_wizard.py --create-base` 会自动创建表、视图和仪表盘（默认放在「我的空间/jira-daily-board」）
- 可选：把 `site_defaults.example.json` 复制为 `site_defaults.json`，填好本单位的 Jira 地址、飞书域名、lark-cli 路径和 profile，团队成员就不用每次输入。`site_defaults.json` 已在 `.gitignore` 中排除，不会被提交

## 快速开始

### 1. 配置

Jira 授权三选一：

| 方式 | 说明 |
|---|---|
| 浏览器登录（推荐） | 用户在浏览器中登录 Jira，助手在页面里自动生成一个 Personal Access Token；密码不经过对话 |
| Personal Access Token | Jira 个人设置 → Personal Access Tokens 生成 |
| 工号 + 密码 | 可直接在对话里给助手（经标准输入传给向导，不进命令行），或自己在终端输入；DPAPI 加密保存到本机 |

无论哪种方式，取数都由本机 Python 按 JQL 直接调 Jira REST API 完成（分页 500 条/次），数据直接写盘，不经过浏览器。

```bash
python setup_wizard.py --auth pat --pat-token <TOKEN> \
  --jql-url "https://<jira>/issues/?jql=..." \
  --create-base \
  --weeks 9
```

- `--jql-url` 支持 `?jql=...` 和保存的筛选器 `?filter=12345`，也可以用 `--jql` 直接给 JQL
- `--weeks`：回溯周数，可选 2/4/9/13/26/全部
- `--show` 查看配置，`--check` 检查是否完整，`--reset` 清除配置
- `--create-base` 默认在「我的空间」根目录的 `jira-daily-board` 文件夹里创建；用 `--feishu-folder "项目/看板"` 或飞书文件夹链接指定其他位置。重复执行会复用同名多维表格
- 已有多维表格时可改用 `--base-url` / `--detail-table-url` / `--snapshot-table-url`
- `--check-jql` 只校验 JQL（查命中条数）；退出码：0 成功；1 缺必填项；2 Jira 授权验证失败；3 连不上 Jira；4 飞书多维表格创建失败；5 JQL 有误

### 2. 更新看板

```bash
python daily_board.py
```

其他参数：`--check-auth` 只检查授权，`--print-jql` 打印实际 JQL，`--skip-fetch` 复用已有数据。

Token 过期或密码失效时脚本会输出 `__AUTH_EXPIRED__`，助手据此引导重新授权，详见 [`SKILL.md`](SKILL.md#输出标记与处理)。

## 统计口径

| 闭环状态 | Jira 状态 | 时效锚点 | 预警 | 超时 |
|---|---|---|---|---|
| 0/4 | 待办 / 已分配 | 创建日期 | > 1 天 | > 3 天（严重超时） |
| 1/4 | 分析中 | 转分析中日期 | > 7 天 | > 12 天 |
| 2/4 | 修复中 | 转修复中日期 | > 7 天 | > 12 天 |
| 3/4 | 验收中 / 审核中 | 转验收/审核日期 | > 7 天 | > 12 天 |
| 4/4 | 完成 | 不计 | — | — |
| 挂起 / 跟踪中 | 挂起 / 申请挂起中 / 跟踪中 | 不计 | — | — |

- **未闭环** = 0/4 + 1/4 + 2/4
- **今日解决** = 当天首次到达 3/4 及以上（当前仍在 3/4 或 4/4）
- 阈值在 [`metrics.py`](metrics.py) 顶部常量中调整；飞书快照表和文本摘要共用同一套计算

## 文件说明

| 文件 | 用途 |
|---|---|
| [`SKILL.md`](SKILL.md) | SKILL 定义：引导流程、表结构、口径、输出标记 |
| `setup_wizard.py` | 首次配置向导（含 Jira 授权验证） |
| `daily_board.py` | 每日入口：取数 → 同步 → 摘要 |
| `jira_client.py` | Jira REST 授权、分页取数、评论、时间窗 JQL |
| `metrics.py` | 闭环阶段、时效、统计口径 |
| `sync_to_base.py` | 通过 lark-cli 同步到飞书多维表格 |
| `feishu_setup.py` | 自动创建文件夹、多维表格、两张表、视图和仪表盘 |
| `lark_cli.py` | lark-cli 调用封装 |
| `site_defaults.example.json` | 站点默认值模板：复制为 `site_defaults.json` 填写本单位的 Jira 地址、飞书域名、lark-cli 路径/profile |
| `config_manager.py` | 配置读写、链接解析、凭证加解密 |
| `fetch_jira.py` | 独立取数调试工具 |

## 注意事项

- 配置保存在 `~/.jira_board_config.json`，含 PAT 或加密凭证，不要外发或提交到仓库。
- `_work/`（原始数据）和 `out/`（摘要）已在 `.gitignore` 中排除。
- 问题明细表每次同步会整体替换，飞书里对明细记录的手工修改不会保留；需要手工标注的信息请放在单独的表里关联。
