#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日入口：读配置 → 取数 → 同步到飞书多维表格 → 写摘要

  python daily_board.py                         # 用 ~/.jira_board_config.json
  python daily_board.py --skip-fetch            # 不重新取数，复用 _work/issues.json
  python daily_board.py --check-auth            # 只检查授权状态
  python daily_board.py --print-jql             # 打印实际使用的 JQL（含时间窗）

取数一律由本机 Python 按 JQL 调 Jira REST API 完成（分页 500 条/次），数据直接写盘，不经过浏览器和助手。

给助手识别的输出标记：
  __AUTH_EXPIRED__            Jira 授权失效（Token 过期或被撤销），需要重新授权

产物在 ./out/ ：看板摘要_YYYY-MM-DD.txt
"""
import argparse, datetime as dt, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
WORK = os.path.join(HERE, "_work")
ISSUES = os.path.join(WORK, "issues.json")
COMMENTS = os.path.join(WORK, "comments.json")

sys.path.insert(0, HERE)
import config_manager as cm
import jira_client as jc
import metrics
import sync_to_base


# ─── 授权 ───────────────────────────────────────────────────────────

def check_auth(cfg):
    """返回 (ok, message)"""
    try:
        _, me = jc.login(cfg.get("jira_base") or jc.DEFAULT_BASE, jc.auth_candidates(cfg, cm.decrypt_creds))
        return (True, f"{me.get('displayName', '')} ({me.get('name', '')})")
    except jc.JiraAuthError as e:
        return (False, f"授权已过期，请重新配置：{e}")
    except (jc.JiraError, RuntimeError) as e:
        return (False, str(e))


# ─── 取数 ───────────────────────────────────────────────────────────

def fetch_via_rest(cfg, jql):
    """PAT / credentials 模式"""
    base = (cfg.get("jira_base") or jc.DEFAULT_BASE).rstrip("/")
    try:
        candidates = jc.auth_candidates(cfg, cm.decrypt_creds)
    except RuntimeError as e:
        raise jc.JiraAuthError(str(e))
    auth, me = jc.login(base, candidates)
    print(f"[jira] 登录成功: {me.get('displayName')} ({me.get('name')})", flush=True)

    extra_ids = jc.resolve_extra_fields(base, auth, cfg.get("extra_fields"))
    issues, total = jc.fetch_issues(base, auth, jql, extra_ids=extra_ids)
    jc.write_issues(ISSUES, base, jql, total, issues, extra_ids=extra_ids)
    print(f"[jira] 已写入 {ISSUES}: {len(issues)} 条", flush=True)

    keys = jc.comment_keys(issues, "open")
    print(f"[jira] 需要抓评论的单子: {len(keys)}", flush=True)
    comments = jc.fetch_comments(base, auth, keys)
    jc.write_json(COMMENTS, comments)
    print(f"[jira] 已写入 {COMMENTS}: {len(comments)} 条", flush=True)


# ─── 摘要 ───────────────────────────────────────────────────────────

def summary_text(st, jql_total):
    a = lambda m: "  ".join(f"{k}={v}" for k, v in sorted(m.items()))
    return "\n".join([
        f"口径日期 {st['date']} ｜ JQL 命中 {jql_total} 条",
        f"今日新增 {st['today_new']} ｜ 今日解决(转3/4+) {st['today_done']} ｜ "
        f"本周新增 {st['week_new']} ｜ 本周解决 {st['week_done']}",
        f"未达3/4 {st['unclosed']} ｜ 挂起/跟踪 {st['parked']} ｜ 超时 {st['timeout']} ｜ 预警 {st['warn']}",
        "状态分布 " + a(st["by_stage"]),
        "等级分布 " + a(st["by_sev"]),
        "周分桶   " + a(st["by_week"]),
    ])


# ─── 主流程 ─────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-fetch", action="store_true", help="复用已有数据")
    ap.add_argument("--check-auth", action="store_true", help="只检查授权")
    ap.add_argument("--print-jql", action="store_true", help="打印实际 JQL 后退出")
    ap.add_argument("--keep", type=int, default=30, help="保留最近 N 份摘要")
    a = ap.parse_args()

    cfg = cm.load()
    if a.check_auth:
        auth_ok, msg = check_auth(cfg)
        print(f"[daily] 授权状态: {'✅' if auth_ok else '❌'} {msg}")
        if not auth_ok:
            print("__AUTH_EXPIRED__")
        sys.exit(0 if auth_ok else 1)

    ok, errors = cm.validate(cfg)
    if not ok:
        print("[daily] 配置不完整，请先运行 setup_wizard.py：")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    today = dt.date.today()
    jql, start = jc.windowed_jql(cfg["jql"], cfg.get("weeks", 9), today)
    if a.print_jql:
        print(jql)
        return
    print(f"[board] 时间窗：{start} → {today}（{cfg['weeks']} 周）" if start else "[board] 时间窗：全部数据")

    os.makedirs(OUT, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)

    try:
        if not a.skip_fetch:
            fetch_via_rest(cfg, jql)
        if not os.path.exists(ISSUES):
            raise jc.JiraError("没有可用的 issues.json，请去掉 --skip-fetch 重新取数")
    except jc.JiraAuthError as e:
        print(f"[daily] !! 授权已过期: {e}\n[daily] 请重新授权 Jira（浏览器登录方式会重新生成 Token）")
        print("__AUTH_EXPIRED__")
        sys.exit(1)
    except (jc.JiraError, RuntimeError) as e:
        print(f"[daily] !! 取数失败: {e}")
        sys.exit(1)

    print("\n[sync] 同步到飞书多维表格…", flush=True)
    try:
        stamp, st = sync_to_base.sync(cfg, ISSUES, COMMENTS)
    except sync_to_base.LarkError as e:
        print(f"[daily] !! 同步失败: {e}")
        sys.exit(1)

    jql_total = json.load(open(ISSUES, encoding="utf-8")).get("total")
    body = summary_text(st, jql_total)
    txt = os.path.join(OUT, f"看板摘要_{stamp}.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write(body + "\n")
    print("\n----- 摘要 -----\n" + body)

    old_files = sorted(glob.glob(os.path.join(OUT, "看板摘要_*.txt")))
    for old in (old_files[:-a.keep] if a.keep > 0 else []):
        try:
            os.remove(old)
        except OSError:
            pass

    print(f"\n[daily] 摘要：{txt}")
    print(f"[daily] 飞书多维表格：{cm.base_url(cfg)}")


if __name__ == "__main__":
    main()
