#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日入口：读配置 → 取数 → 同步到飞书多维表格 → 写摘要

  python daily_board.py                         # 用 ~/.jira_board_config.json
  python daily_board.py --skip-fetch            # 不重新取数（浏览器模式下若有更新的浏览器结果仍会导入）
  python daily_board.py --check-auth            # 只检查授权状态
  python daily_board.py --emit-browser-script   # 浏览器模式：生成带当天 JQL 的取数脚本
  python daily_board.py --print-jql             # 打印实际使用的 JQL（含时间窗）

给 助手 识别的输出标记：
  __AUTH_EXPIRED__            Jira 授权失效，需要重新登录 / 重新配置
  __BROWSER_FETCH_REQUIRED__  浏览器模式下缺少当天的浏览器取数结果
  __BROWSER_SCRIPT__ <path>   --emit-browser-script 生成的脚本路径

产物在 ./out/ ：看板摘要_YYYY-MM-DD.txt
"""
import argparse, datetime as dt, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
WORK = os.path.join(HERE, "_work")
ISSUES = os.path.join(WORK, "issues.json")
COMMENTS = os.path.join(WORK, "comments.json")
BROWSER_RESULT = os.path.join(WORK, "browser_fetch_result.json")
BROWSER_TEMPLATE = os.path.join(HERE, "jira_fetch_browser.js")
BROWSER_SCRIPT = os.path.join(WORK, "jira_fetch_browser.generated.js")
CONFIG_SLOT = "/*__CONFIG__*/ (window.__JIRA_FETCH_CONFIG || {})"

sys.path.insert(0, HERE)
import config_manager as cm
import jira_client as jc
import metrics
import sync_to_base


class BrowserFetchRequired(RuntimeError):
    pass


# ─── 授权 ───────────────────────────────────────────────────────────

def check_auth(cfg):
    """返回 (ok, message)；浏览器模式返回 ("browser_check", ...) 交给 助手 验证"""
    method = cfg.get("auth_method", "")
    if method == "browser":
        return ("browser_check", "浏览器授权需要 助手 通过 browser evaluate 验证")
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


def emit_browser_script(jql, cfg):
    tpl = open(BROWSER_TEMPLATE, encoding="utf-8").read()
    if CONFIG_SLOT not in tpl:
        raise RuntimeError(f"{BROWSER_TEMPLATE} 中找不到配置占位符")
    known = dict(jc.KNOWN_EXTRA_IDS)
    known.update({k: v for k, v in (cfg.get("extra_fields") or {}).items() if v})
    conf = json.dumps({"jql": jql, "fields": jc.FIELDS, "page": jc.PAGE_SIZE,
                       "commentBatch": jc.COMMENT_BATCH,
                       "extraFields": jc.EXTRA_FIELDS, "extraIds": known}, ensure_ascii=False)
    os.makedirs(WORK, exist_ok=True)
    with open(BROWSER_SCRIPT, "w", encoding="utf-8") as f:
        f.write(tpl.replace(CONFIG_SLOT, conf))
    return BROWSER_SCRIPT


def import_browser_result(cfg, expected_jql, today, allow_stale=False):
    """浏览器模式：把 助手 写入的 browser_fetch_result.json 转成 issues.json / comments.json"""
    if not os.path.exists(BROWSER_RESULT):
        raise BrowserFetchRequired("没有浏览器取数结果，请先在浏览器中登录 Jira 并执行取数脚本")
    data = json.load(open(BROWSER_RESULT, encoding="utf-8"))
    if not data.get("ok"):
        error = data.get("error", "未知错误")
        if error == "AUTH_EXPIRED":
            raise jc.JiraAuthError("浏览器登录已过期，请重新登录 Jira")
        raise jc.JiraError(f"浏览器取数失败: {error}")

    fetched_at = data.get("fetched_at") or ""
    if fetched_at[:10] != today.isoformat() and not allow_stale:
        raise BrowserFetchRequired(f"浏览器取数结果不是今天的（fetched_at={fetched_at or '无'}），请重新取数")
    jql = data.get("jql", "")
    if jql and jql != expected_jql:
        print(f"[jira] [warn] 浏览器取数用的 JQL 与当前配置不一致：\n  浏览器: {jql}\n  配置  : {expected_jql}")

    issues = data.get("issues", [])
    base = data.get("base_url") or cfg.get("jira_base") or jc.DEFAULT_BASE
    jc.write_issues(ISSUES, base, jql, data.get("total", len(issues)), issues, fetched_at or None,
                    data.get("extra_field_ids") or {})
    jc.write_json(COMMENTS, data.get("comments", {}))
    print(f"[jira] 浏览器数据已导入 {ISSUES}: {len(issues)} 条，评论 {len(data.get('comments', {}))} 条", flush=True)


def newer(a, b):
    return os.path.exists(a) and (not os.path.exists(b) or os.path.getmtime(a) > os.path.getmtime(b))


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
    ap.add_argument("--emit-browser-script", action="store_true", help="生成浏览器取数脚本后退出")
    ap.add_argument("--print-jql", action="store_true", help="打印实际 JQL 后退出")
    ap.add_argument("--allow-stale", action="store_true", help="浏览器模式下允许使用非当天的取数结果")
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
    if a.emit_browser_script:
        print(f"__BROWSER_SCRIPT__ {emit_browser_script(jql, cfg)}")
        return
    print(f"[board] 时间窗：{start} → {today}（{cfg['weeks']} 周）" if start else "[board] 时间窗：全部数据")

    os.makedirs(OUT, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)

    method = cfg.get("auth_method", "")
    try:
        if method == "browser":
            # 浏览器模式的"取数"就是导入 助手 写好的浏览器结果；
            # --skip-fetch 时只有在浏览器结果比 issues.json 新的情况下才导入
            if not a.skip_fetch or newer(BROWSER_RESULT, ISSUES):
                import_browser_result(cfg, jql, today, a.allow_stale)
        elif not a.skip_fetch:
            fetch_via_rest(cfg, jql)
        if not os.path.exists(ISSUES):
            raise BrowserFetchRequired("没有可用的 issues.json") if method == "browser" \
                else jc.JiraError("没有可用的 issues.json，请去掉 --skip-fetch 重新取数")
    except jc.JiraAuthError as e:
        print(f"[daily] !! 授权已过期: {e}\n[daily] 请重新登录 Jira")
        print("__AUTH_EXPIRED__")
        sys.exit(1)
    except BrowserFetchRequired as e:
        print(f"[daily] !! {e}")
        print("__BROWSER_FETCH_REQUIRED__")
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
