#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次配置向导：收集用户配置并保存到 ~/.jira_board_config.json

由 助手 调用，参数从对话中收集的用户回答传入。

用法：
  python setup_wizard.py --auth browser --jql-url "https://jira.example.com/issues/?jql=..." --base-url "https://xxx.feishu.cn/base/TOKEN"
  python setup_wizard.py --auth pat --pat-token xxx --jql-file jql.txt --base-token TOKEN
  echo 密码 | python setup_wizard.py --auth credentials --user 工号 --password-stdin   # 助手 在对话中收集密码后调用
  python setup_wizard.py --auth credentials --user 工号      # 用户自己在终端运行时，交互输入密码
  python setup_wizard.py --create-base                          # 在「我的空间/jira-daily-board」下自动建多维表格
  python setup_wizard.py --create-base --feishu-folder "项目/看板"  # 或指定路径 / 飞书文件夹链接
  python setup_wizard.py --show   # 显示当前配置
  python setup_wizard.py --check  # 检查配置是否完整
  python setup_wizard.py --reset  # 清除配置

退出码：0 成功；1 配置不完整；2 Jira 授权验证失败（配置已保存，需重新授权）；3 连不上 Jira；
        4 飞书多维表格创建失败（输出 __FEISHU_SETUP_FAILED__）；5 JQL 有误（--check-jql，输出 __JQL_ERROR__）
"""
import argparse, getpass, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config_manager as cm
import jira_client as jc


def verify_auth(cfg):
    """验证授权，返回 (status, message)，status 为 ok / auth / network。
    PAT 验证通过后记下实际可用的头格式。"""
    method = cfg.get("auth_method")
    if method == "browser":
        return "ok", "浏览器授权需要由 助手 在浏览器中验证（fetch /rest/api/2/myself）"
    base = cfg.get("jira_base") or jc.DEFAULT_BASE
    try:
        auth, me = jc.login(base, jc.auth_candidates(cfg, cm.decrypt_creds))
    except jc.JiraAuthError as e:
        return "auth", f"验证失败：{e}"
    except (jc.JiraError, RuntimeError) as e:
        return "network", f"连不上 Jira，请检查网络/VPN 后重试：{e}"
    if method == "pat":
        cfg["pat_scheme"] = auth.split(" ", 1)[0]
    return "ok", f"验证成功：{me.get('displayName', '')} ({me.get('name', '')})"


def check_jql(cfg):
    """校验 JQL 并保存（含已收集的授权信息），让 助手 不必再问用户"确认 JQL 吗"。
    退出码 0 + __JQL_OK__ <条数>；5 + __JQL_ERROR__（JQL 写错）；2 授权失败；3 连不上 Jira。
    浏览器模式无法在这里查，输出 __JQL_CHECK_IN_BROWSER__ <JQL>，由 助手 在浏览器里查条数。"""
    if not cfg.get("jql"):
        print("__JQL_ERROR__ 没有提取到 JQL（链接里需要 jql= 或 filter= 参数）")
        sys.exit(5)
    if not cfg.get("jira_base"):
        print("__JQL_ERROR__ 缺少 Jira 地址：请提供 Jira 筛选链接，或用 --jira-base 指定")
        sys.exit(5)
    jql, start = jc.windowed_jql(cfg["jql"], cfg.get("weeks", 9))
    cm.save(cfg)
    print(f"[setup] JQL：{cfg['jql']}")
    if cfg.get("auth_method") == "browser":
        print(f"__JQL_CHECK_IN_BROWSER__ {jql}")
        return
    base = cfg.get("jira_base") or jc.DEFAULT_BASE
    try:
        auth, _ = jc.login(base, jc.auth_candidates(cfg, cm.decrypt_creds))
        total = jc.count_issues(base, auth, jql)
    except jc.JiraAuthError as e:
        print(f"[setup] ⚠️ 验证失败：{e}\n__AUTH_FAILED__")
        sys.exit(2)
    except jc.JiraError as e:
        if "JQL" in str(e):
            print(f"__JQL_ERROR__ {e}")
            sys.exit(5)
        print(f"[setup] ⚠️ 连不上 Jira：{e}\n__JIRA_UNREACHABLE__")
        sys.exit(3)
    except RuntimeError as e:
        print(f"[setup] ⚠️ {e}\n__AUTH_FAILED__")
        sys.exit(2)
    window = f"（{start} 至今）" if start else "（全部时间）"
    print(f"__JQL_OK__ {total}")
    print(f"[setup] ✅ JQL 有效，命中 {total} 条{window}")


def main():
    ap = argparse.ArgumentParser(description="Jira 看板首次配置向导")
    ap.add_argument("--show", action="store_true", help="显示当前配置")
    ap.add_argument("--reset", action="store_true", help="清除配置")
    ap.add_argument("--check", action="store_true", help="检查配置是否完整")

    ap.add_argument("--auth", choices=["browser", "pat", "credentials"], help="授权方式")
    ap.add_argument("--jira-base", default="", help="Jira 地址")
    ap.add_argument("--pat-token", default="", help="PAT Token")
    ap.add_argument("--user", default="", help="工号")
    ap.add_argument("--password", default="",
                    help="密码（会出现在进程列表/命令历史里，优先用 --password-stdin）")
    ap.add_argument("--password-stdin", action="store_true", help="从标准输入读取密码（第一行）")
    ap.add_argument("--jql", default="", help="JQL 查询语句")
    ap.add_argument("--jql-url", default="", help="Jira 筛选链接（?jql=... 或 ?filter=ID）")
    ap.add_argument("--jql-file", default="", help="从文件读取 JQL")
    ap.add_argument("--base-token", default="", help="飞书多维表格 Token")
    ap.add_argument("--base-url", default="", help="已有的飞书多维表格 URL（自动提取 Token）")
    ap.add_argument("--create-base", action="store_true",
                    help="自动创建（或复用）多维表格、两张表、视图和仪表盘")
    ap.add_argument("--feishu-folder", default="",
                    help="创建位置：飞书文件夹链接，或 / 分隔的路径（相对「我的空间」）；默认 jira-daily-board")
    ap.add_argument("--base-name", default="", help="多维表格名称（默认「Jira 问题日清看板」）")
    ap.add_argument("--repair-base", action="store_true",
                    help="补全当前配置（或 --base-url 指定）的多维表格：缺的字段、视图、仪表盘组件")
    ap.add_argument("--check-jql", action="store_true",
                    help="只校验 JQL（加上回溯周数时间窗后查命中条数），输出 __JQL_OK__ <条数> 或 __JQL_ERROR__")
    ap.add_argument("--detail-table", default="", help="问题明细表 ID")
    ap.add_argument("--snapshot-table", default="", help="日清快照表 ID")
    ap.add_argument("--detail-table-url", default="", help="打开「问题明细表」时的链接（自动提取 table=tbl…）")
    ap.add_argument("--snapshot-table-url", default="", help="打开「日清快照表」时的链接（自动提取 table=tbl…）")
    ap.add_argument("--view-today-updated", default=None, help="「今日更新」视图 ID（传空字符串=不刷新）")
    ap.add_argument("--view-today-created", default=None, help="「今日新增」视图 ID（传空字符串=不刷新）")
    ap.add_argument("--lark-cli", default="", help="lark-cli 可执行文件路径")
    ap.add_argument("--lark-profile", default="", help="lark-cli profile")
    ap.add_argument("--mail-to", default="", help="邮件收件人")
    ap.add_argument("--schedule-time", default="", help="定时时间 HH:MM")
    ap.add_argument("--title", default="", help="看板标题")
    ap.add_argument("--weeks", default="", help="回溯周数")
    ap.add_argument("--sender-email", default="", help="发件人邮箱")
    a = ap.parse_args()

    if a.show:
        print(cm.show_config(cm.load()))
        return

    if a.reset:
        if os.path.exists(cm.CONFIG_PATH):
            os.remove(cm.CONFIG_PATH)
            print("[setup] 配置已清除")
        else:
            print("[setup] 没有配置文件")
        return

    if a.check:
        if cm.is_configured():
            print("[setup] 配置完整")
            print(cm.show_config(cm.load()))
        else:
            print("[setup] 配置不完整，需要首次配置")
            sys.exit(1)
        return

    cfg = cm.load()

    if a.auth:
        cfg["auth_method"] = a.auth
    if a.jira_base:
        cfg["jira_base"] = a.jira_base.rstrip("/")
    if a.pat_token:
        cfg["pat_token"] = a.pat_token.strip()
        cfg.pop("pat_scheme", None)

    if a.user:
        if a.password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
        else:
            password = a.password or os.environ.get("JIRA_PASS", "")
        if not password and sys.stdin.isatty():
            password = getpass.getpass("Jira 密码（输入不回显）: ")
        if not password:
            sys.exit("[setup] 缺少密码：请用 --password-stdin 传入、设置环境变量 JIRA_PASS，或在终端输入")
        cfg["_encrypted_creds"] = cm.encrypt_creds(a.user, password)
        cfg["_jira_user"] = a.user

    if a.jql:
        cfg["jql"] = a.jql.strip()
    elif a.jql_url:
        if not cfg.get("jira_base"):
            cfg["jira_base"] = cm.extract_jira_base(a.jql_url)
        extracted = cm.extract_jql_from_url(a.jql_url)
        if extracted:
            cfg["jql"] = extracted
            print(f"[setup] 从 URL 提取 JQL: {extracted[:80]}{'...' if len(extracted) > 80 else ''}")
        else:
            print("[setup] 警告：无法从 URL 提取 JQL（需要包含 jql= 或 filter= 参数），请检查链接格式")
    elif a.jql_file:
        if os.path.exists(a.jql_file):
            cfg["jql"] = open(a.jql_file, encoding="utf-8").read().strip()
        else:
            print(f"[setup] 警告：文件不存在 {a.jql_file}")

    if a.base_token:
        cfg["base_token"] = a.base_token.strip()
    elif a.base_url:
        token = cm.extract_base_token_from_url(a.base_url)
        if token:
            cfg["base_token"] = token
            cfg["feishu_host"] = cm.extract_feishu_host(a.base_url) or cfg.get("feishu_host")
            print(f"[setup] 从 URL 提取 base_token: {token}")
        else:
            print("[setup] 警告：无法从 URL 提取 base_token，请检查链接格式")

    for arg, key in (("detail_table", "detail_table"), ("snapshot_table", "snapshot_table"),
                     ("lark_cli", "lark_cli"), ("lark_profile", "lark_profile"),
                     ("mail_to", "mail_to"), ("schedule_time", "schedule_time"),
                     ("title", "title"), ("sender_email", "sender_email")):
        v = getattr(a, arg)
        if v:
            cfg[key] = v.strip()
    for arg in ("view_today_updated", "view_today_created"):
        v = getattr(a, arg)
        if v is not None:
            cfg[arg] = v.strip()
    for arg, key in (("detail_table_url", "detail_table"), ("snapshot_table_url", "snapshot_table")):
        url = getattr(a, arg)
        if url:
            tid = cm.extract_table_id(url)
            if tid:
                cfg[key] = tid
                print(f"[setup] 从 URL 提取 {key}: {tid}")
            else:
                print(f"[setup] 警告：链接里没有 table=tbl… 参数，{key} 未修改")
            if not cfg.get("base_token"):
                cfg["base_token"] = cm.extract_base_token_from_url(url)
    if a.weeks:
        cfg["weeks"] = cm.parse_weeks(a.weeks)

    if a.check_jql:
        check_jql(cfg)
        return

    if a.create_base or a.repair_base:
        import feishu_setup
        if a.repair_base and not cfg.get("base_token"):
            sys.exit("[setup] --repair-base 需要先配置多维表格（或同时传 --base-url）")
        try:
            created = feishu_setup.setup(cfg, a.feishu_folder, a.base_name or feishu_setup.DEFAULT_BASE_NAME,
                                         existing_base=cfg["base_token"] if a.repair_base else "")
        except Exception as e:
            print(f"[setup] 飞书多维表格创建失败：{e}")
            print("__FEISHU_SETUP_FAILED__")
            sys.exit(4)
        cfg.update(created)
        cm.save(cfg)
        print(f"[setup] 飞书多维表格已就绪：{cm.base_url(cfg)}")

    ok, errors = cm.validate(cfg)
    if not ok and (a.create_base or a.repair_base) and not (a.auth or a.jql or a.jql_url or a.jql_file):
        # 引导第 ③ 步单独建表时，其余配置还没收集，先保存已有部分
        print("[setup] 飞书部分已保存，其余配置待补全")
        return
    if not ok:
        print("[setup] 配置校验失败：")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    status, auth_msg = verify_auth(cfg)
    cm.save(cfg)
    print("\n" + cm.show_config(cfg))
    if status == "ok":
        print(f"\n[setup] 🔐 {auth_msg}")
        print("[setup] ✅ 配置完成！")
    elif status == "auth":
        print(f"\n[setup] ⚠️ {auth_msg}")
        print("__AUTH_FAILED__")
        sys.exit(2)
    else:
        print(f"\n[setup] ⚠️ {auth_msg}")
        print("__JIRA_UNREACHABLE__")
        sys.exit(3)


if __name__ == "__main__":
    main()
