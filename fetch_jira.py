#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立取数工具（调试用）：从内网 Jira Server 拉取问题数据 -> issues.json / comments.json

日常流程不需要它，daily_board.py 会直接调用 jira_client。

用法:
  python fetch_jira.py --jql-file jql.txt --out issues.json
  python fetch_jira.py --jql "project = ABC AND ..." --user xxx      # 密码读环境变量 JIRA_PASS
  python fetch_jira.py --jql "..." --pat-token xxx
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config_manager as cfgm
import jira_client as jc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("JIRA_BASE", cfgm.DEFAULTS["jira_base"]))
    ap.add_argument("--user", default=os.environ.get("JIRA_USER", ""))
    ap.add_argument("--password", default=os.environ.get("JIRA_PASS", ""))
    ap.add_argument("--pat-token", default=os.environ.get("JIRA_PAT", ""))
    ap.add_argument("--jql", default="")
    ap.add_argument("--jql-file", default="")
    ap.add_argument("--weeks", type=int, default=0, help="加 created 时间窗（0=不加）")
    ap.add_argument("--out", default="issues.json")
    ap.add_argument("--comments-out", default="comments.json")
    ap.add_argument("--comment-scope", default="open", choices=["all", "open", "none"],
                    help="为哪些单子抓评论：open=未闭环+近7天新增")
    ap.add_argument("--page", type=int, default=jc.PAGE_SIZE)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    if not a.base_url:
        sys.exit("必须提供 --base-url 或环境变量 JIRA_BASE（Jira 地址）")
    if a.jql_file:
        a.jql = open(a.jql_file, encoding="utf-8").read().strip()
    if not a.jql:
        sys.exit("必须提供 --jql 或 --jql-file")
    if a.pat_token:
        candidates = jc.auth_candidates({"auth_method": "pat", "pat_token": a.pat_token})
    elif a.user and a.password:
        candidates = [jc.basic(a.user, a.password)]
    else:
        sys.exit("必须提供 --pat-token，或 --user/--password（环境变量 JIRA_USER/JIRA_PASS）")

    jql, _ = jc.windowed_jql(a.jql, a.weeks)
    try:
        auth, me = jc.login(a.base_url, candidates)
        print(f"[jira] 登录成功: {me.get('displayName')} ({me.get('name')})", flush=True)
        issues, total = jc.fetch_issues(a.base_url, auth, jql, page=a.page, limit=a.limit or None)
        jc.write_issues(a.out, a.base_url, jql, total, issues)
        print(f"[jira] 已写入 {a.out}: {len(issues)} 条", flush=True)
        keys = jc.comment_keys(issues, a.comment_scope)
        if keys:
            print(f"[jira] 需要抓评论的单子: {len(keys)}", flush=True)
            cm = jc.fetch_comments(a.base_url, auth, keys)
            jc.write_json(a.comments_out, cm)
            print(f"[jira] 已写入 {a.comments_out}: {len(cm)} 条", flush=True)
    except jc.JiraError as e:
        sys.exit(f"[jira] !! {e}")


if __name__ == "__main__":
    main()
