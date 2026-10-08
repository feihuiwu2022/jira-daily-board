#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 _work/issues.json 同步到飞书多维表格（通过 lark-cli，user 身份）

  python sync_to_base.py                       # 表 ID 等读 ~/.jira_board_config.json
  python sync_to_base.py --base-token XXX --detail-table tblXXX --snapshot-table tblYYY

问题明细表：先写入新数据，全部成功后再删除旧数据；写入失败会回滚本次新写入的记录，
旧数据保持不动（不会出现"表被清空但没写进去"的情况）。
日清快照表：按日期 upsert。
任何一步失败都以非 0 退出码结束。
"""
import argparse, datetime as dt, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "_work")
sys.path.insert(0, HERE)
import config_manager as cm
import metrics
from lark_cli import Lark, LarkError
from jira_client import EXTRA_FIELDS

DETAIL_FIELDS = ["编号", "等级", "闭环状态", "时效标签", "停留天数", "责任工程师", "功能模块",
                 "概要", "创建日期", "更新时间", "周分桶", "最新进展", "Jira状态"]
EXTRA_COLUMNS = list(EXTRA_FIELDS)   # 问题来源 / 缺陷类型 / 问题原因大类
SNAPSHOT_FIELDS = ["日期", "问题总数", "今日新增", "今日解决", "本周新增", "本周解决",
                   "超时数", "预警数", "未闭环数"]
SEV_OPTIONS = ("S", "A", "B+", "B", "C")
STAGE_OPTIONS = ("0/4", "1/4", "2/4", "3/4", "4/4", "挂起", "跟踪中")
AG_OPTIONS = ("正常", "预警", "超时", "严重超时")


# ─── 同步 ───────────────────────────────────────────────────────────

def detail_rows(recs, jira_base):
    rows = []
    for r in recs:
        url = f"{jira_base.rstrip('/')}/browse/{r['key']}"
        updated = r["updated"].replace("T", " ").split("+")[0].split(".")[0] if r.get("updated") else ""
        rows.append([
            f"[{r['key']}]({url})",
            r["sev"] if r["sev"] in SEV_OPTIONS else "B",
            r["stage"] if r["stage"] in STAGE_OPTIONS else "0/4",
            r["ag"] if r["ag"] in AG_OPTIONS else "正常",
            r["agDays"], r["eng"], r["mod"],
            r["summary"][:500],
            f"{r['created']} 00:00:00" if r["created"] else "",
            updated, r["week"], (r["progress"] or "")[:500], r["status"] or "",
        ] + [(r.get("extra") or {}).get(c, "") for c in EXTRA_COLUMNS])
    return rows


def writable_columns(lark, table_id):
    """问题明细表里实际存在的扩展列。老表没有这些列时跳过，不影响同步。"""
    try:
        names = lark.field_names(table_id)
    except LarkError as e:
        print(f"  [warn] 读取字段列表失败，扩展列不写入: {e}")
        return []
    missing = [c for c in EXTRA_COLUMNS if c not in names]
    if missing:
        print(f"  [warn] 问题明细表缺少字段 {missing}，这些列不写入"
              f"（运行 setup_wizard.py --repair-base 可自动补齐）")
    return [c for c in EXTRA_COLUMNS if c in names]


def replace_detail(lark, table_id, rows):
    """先写新数据、再删旧数据；写入失败则删掉本次已写入的部分，旧数据不动"""
    extra = writable_columns(lark, table_id)
    fields = DETAIL_FIELDS + extra
    keep = len(DETAIL_FIELDS)
    idx = [keep + EXTRA_COLUMNS.index(c) for c in extra]
    rows = [r[:keep] + [r[i] for i in idx] for r in rows]
    old_ids = lark.list_record_ids(table_id)
    print(f"  表中现有 {len(old_ids)} 条，开始写入 {len(rows)} 条新数据")
    try:
        lark.create_rows(table_id, fields, rows, "问题明细")
    except LarkError:
        print("  [rollback] 写入失败，回滚本次已写入的记录，保留旧数据…")
        try:
            old = set(old_ids)
            lark.delete_records(table_id, [x for x in lark.list_record_ids(table_id) if x not in old], "回滚")
        except LarkError as e:
            print(f"  [rollback] 回滚失败，表中可能有重复数据，请手动检查: {e}")
        raise
    lark.delete_records(table_id, old_ids, "删除旧数据")


def upsert_snapshot(lark, table_id, now, row):
    prev_day = (dt.date.fromisoformat(now) - dt.timedelta(days=1)).isoformat()
    next_day = (dt.date.fromisoformat(now) + dt.timedelta(days=1)).isoformat()
    flt = {"logic": "and", "conditions": [["日期", "isGreater", f"{prev_day} 23:59:59"],
                                          ["日期", "isLess", f"{next_day} 00:00:01"]]}
    res = lark.run(["base", "+record-list", "--base-token", lark.base, "--table-id", table_id,
                    "--filter-json", json.dumps(flt, ensure_ascii=False), "--limit", "1", "--format", "json"],
                   "查询当日快照")
    ids = (res.get("data", {}) if isinstance(res, dict) else {}).get("record_id_list", [])
    if ids:
        lark.run_with_payload(["base", "+record-batch-update", "--base-token", lark.base, "--table-id", table_id],
                              {"record_id_list": [ids[0]], "patch": dict(zip(SNAPSHOT_FIELDS, row))},
                              "更新当日快照")
        print(f"  [日清快照] 已更新当天记录 {ids[0]}")
    else:
        lark.run_with_payload(["base", "+record-batch-create", "--base-token", lark.base, "--table-id", table_id],
                              {"fields": SNAPSHOT_FIELDS, "rows": [row]}, "追加当日快照")
        print("  [日清快照] 已追加")


def update_views(lark, cfg, now):
    """刷新「今日更新/今日新增」视图的筛选日期；失败只告警，不影响数据同步"""
    today = dt.date.fromisoformat(now)
    prev_day, next_day = (today - dt.timedelta(days=1)).isoformat(), (today + dt.timedelta(days=1)).isoformat()
    views = [
        (cfg.get("view_today_updated"), "今日更新",
         {"logic": "and", "conditions": [["更新时间", "isGreater", f"{now} 00:00:00"]]}),
        (cfg.get("view_today_created"), "今日新增",
         {"logic": "and", "conditions": [["创建日期", "isGreater", f"{prev_day} 23:59:59"],
                                         ["创建日期", "isLess", f"{next_day} 00:00:00"]]}),
    ]
    for view_id, name, flt in views:
        if not view_id:
            continue
        try:
            lark.run(["base", "+view-set-filter", "--base-token", lark.base, "--table-id", cfg["detail_table"],
                      "--view-id", view_id, "--json", json.dumps(flt, ensure_ascii=False)], f"更新视图 {name}",
                     require_ok=False)
            print(f"[view] 「{name}」筛选日期 → {now}")
        except LarkError as e:
            print(f"[view] [warn] {e}")


def sync(cfg, issues_path=None, comments_path=None):
    """执行同步，返回 (now, stats)。失败抛 LarkError。"""
    issues_path = issues_path or os.path.join(WORK, "issues.json")
    comments_path = comments_path or os.path.join(WORK, "comments.json")
    doc = json.load(open(issues_path, encoding="utf-8"))
    comments = json.load(open(comments_path, encoding="utf-8")) if os.path.exists(comments_path) else {}
    now = doc.get("fetched_at", "")[:10] or dt.date.today().isoformat()
    print(f"[sync] 口径日期: {now}, 数据条数: {len(doc['issues'])}")

    recs = metrics.normalize(doc, comments, now)
    st = metrics.compute_stats(recs, now)
    lark = Lark(cfg)
    jira_base = doc.get("base_url") or cfg.get("jira_base") or cm.DEFAULTS["jira_base"]

    print(f"\n[upload] 问题明细表 {cfg['detail_table']}")
    replace_detail(lark, cfg["detail_table"], detail_rows(recs, jira_base))

    print(f"\n[upload] 日清快照表 {cfg['snapshot_table']}（日期={now}）")
    upsert_snapshot(lark, cfg["snapshot_table"], now, [
        f"{now} 00:00:00", st["total"], st["today_new"], st["today_done"], st["week_new"],
        st["week_done"], st["timeout"], st["warn"], st["unclosed"]])

    update_views(lark, cfg, now)
    print(f"\n[sync] 完成！问题明细 {st['total']} 条")
    print(f"[sync] 摘要: 总数={st['total']} 新增={st['today_new']} 解决={st['today_done']} "
          f"超时={st['timeout']} 预警={st['warn']} 未闭环={st['unclosed']}")
    return now, st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-token", default="")
    ap.add_argument("--detail-table", default="")
    ap.add_argument("--snapshot-table", default="")
    a = ap.parse_args()
    cfg = cm.load()
    for k in ("base_token", "detail_table", "snapshot_table"):
        if getattr(a, k):
            cfg[k] = getattr(a, k)
    if not cfg.get("base_token"):
        sys.exit("必须提供 --base-token 或先运行 setup_wizard.py")
    try:
        sync(cfg)
    except LarkError as e:
        print(f"[sync] !! {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
