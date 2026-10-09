#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动创建看板用的飞书多维表格：文件夹 → 多维表格 → 两张表 → 常用视图 → 仪表盘。

默认位置：「我的空间」根目录下的 jira-daily-board 文件夹。
也可以指定：飞书文件夹链接（…/drive/folder/<token>），或以 / 分隔的路径（相对「我的空间」根目录，
不存在的文件夹会逐级创建）。

可重复执行：目标文件夹里已有同名多维表格时直接复用，只补建缺少的表、视图和仪表盘。
"""
import re, sys

from lark_cli import Lark, LarkError
from sync_to_base import SEV_OPTIONS, STAGE_OPTIONS, AG_OPTIONS, EXTRA_COLUMNS

DEFAULT_FOLDER = "jira-daily-board"
DEFAULT_BASE_NAME = "Jira 问题日清看板"
DETAIL_TABLE = "问题明细"
SNAPSHOT_TABLE = "日清快照"
DASHBOARD = "Bug日清看板"

HUES = {
    "S": "Red", "A": "Orange", "B+": "Yellow", "B": "Blue", "C": "Gray",
    "0/4": "Red", "1/4": "Orange", "2/4": "Yellow", "3/4": "Blue", "4/4": "Green",
    "挂起": "Gray", "跟踪中": "Purple",
    "正常": "Green", "预警": "Yellow", "超时": "Orange", "严重超时": "Red",
}


def _select(name, options):
    return {"type": "select", "name": name, "multiple": False,
            "options": [{"name": o, "hue": HUES.get(o, "Blue")} for o in options]}


def _number(name):
    return {"type": "number", "name": name, "style": {"type": "plain", "precision": 0}}


DETAIL_SCHEMA = [
    {"type": "text", "name": "编号", "description": "Jira Key（带链接）"},
    _select("等级", SEV_OPTIONS),
    _select("闭环状态", STAGE_OPTIONS),
    _select("时效标签", AG_OPTIONS),
    _number("停留天数"),
    {"type": "text", "name": "责任工程师"},
    {"type": "text", "name": "功能模块"},
    {"type": "text", "name": "概要"},
    {"type": "datetime", "name": "创建日期", "style": {"format": "yyyy-MM-dd"}},
    {"type": "datetime", "name": "更新时间", "style": {"format": "yyyy-MM-dd HH:mm"}},
    {"type": "text", "name": "周分桶"},
    {"type": "text", "name": "最新进展"},
    {"type": "text", "name": "Jira状态"},
] + [{"type": "text", "name": c} for c in EXTRA_COLUMNS] + [
    {"type": "text", "name": "同步指纹", "description": "同步脚本用来判断这一行是否变化，请勿修改"}]

SNAPSHOT_SCHEMA = [{"type": "datetime", "name": "日期", "style": {"format": "yyyy-MM-dd"}}] + [
    _number(n) for n in ("问题总数", "今日新增", "今日解决", "本周新增", "本周解决", "超时数", "预警数", "未闭环数")]

DEFAULT_VIEW = "问题总表"


def _where(*conds):
    return {"logic": "and", "conditions": list(conds)}


# (视图名, 筛选, 分组字段, 排序 [(字段, 是否降序)])；日期筛选用相对日期 Today，建好后不需要每天改
DETAIL_VIEWS = [
    ("超时预警明细", _where(["时效标签", "intersects", ["预警", "超时", "严重超时"]]), None, [("停留天数", True)]),
    ("未闭环问题", _where(["闭环状态", "intersects", ["0/4", "1/4", "2/4"]]), None, [("停留天数", True)]),
    ("🔴 S级问题", _where(["等级", "intersects", ["S"]]), None, [("创建日期", True)]),
    ("🟠 A级问题", _where(["等级", "intersects", ["A"]]), None, [("创建日期", True)]),
    ("⏰ 严重超时", _where(["时效标签", "intersects", ["严重超时"]]), None, [("停留天数", True)]),
    ("📅 今日更新", _where(["更新时间", "==", "Today"]), None, [("更新时间", True)]),
    ("📊 按功能模块分组", None, "功能模块", []),
    ("📊 按等级分组", None, "等级", []),
    ("📅 今日新增", _where(["创建日期", "==", "Today"]), None, [("创建日期", True)]),
]


def _is(field, value):
    return {"field_name": field, "operator": "is", "value": value}


UNCLOSED_FILTER = {"conjunction": "or", "conditions": [_is("闭环状态", s) for s in ("0/4", "1/4", "2/4")]}


def _dist(field, sort=None):
    g = {"field_name": field, "mode": "integrated"}
    if sort:
        g["sort"] = sort
    return {"table_name": DETAIL_TABLE, "count_all": True, "group_by": [g]}


DASHBOARD_BLOCKS = [
    ("问题总数", "statistics", {"table_name": DETAIL_TABLE, "count_all": True}),
    ("超时问题数", "statistics", {"table_name": DETAIL_TABLE, "count_all": True, "filter": {
        "conjunction": "or", "conditions": [_is("时效标签", "超时"), _is("时效标签", "严重超时")]}}),
    ("预警问题数", "statistics", {"table_name": DETAIL_TABLE, "count_all": True, "filter": {
        "conjunction": "and", "conditions": [_is("时效标签", "预警")]}}),
    ("未闭环数", "statistics", {"table_name": DETAIL_TABLE, "count_all": True, "filter": UNCLOSED_FILTER}),
    ("等级分布", "pie", _dist("等级")),
    ("Jira状态分布", "pie", _dist("Jira状态")),
    ("按周趋势", "column", _dist("周分桶", {"type": "group", "order": "asc"})),
    ("责任工程师未闭环排行", "bar", dict(_dist("责任工程师", {"type": "value", "order": "desc"}),
                                    filter=UNCLOSED_FILTER)),
    ("功能模块分布", "column", _dist("功能模块", {"type": "group", "order": "asc"})),
    ("时效标签分布", "pie", _dist("时效标签")),
    ("问题来源分布", "ring", _dist("问题来源")),
    ("缺陷类型分布", "pie", _dist("缺陷类型")),
    ("问题原因大类", "ring", _dist("问题原因大类")),
]


def _first(d, *keys):
    for k in keys:
        v = (d or {}).get(k)
        if v:
            return v
    return ""


def _json(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


# ─── 文件夹 ─────────────────────────────────────────────────────────

FOLDER_URL_RE = re.compile(r"/drive/folder/([A-Za-z0-9]+)")


def list_children(lark, folder_token):
    items, page_token = [], ""
    while True:
        params = {"folder_token": folder_token, "page_size": 200}
        if page_token:
            params["page_token"] = page_token
        data = lark.data(["drive", "files", "list", "--params", _json(params), "--format", "json"], "读取文件夹")
        items += data.get("files") or []
        page_token = data.get("next_page_token") or ""
        if not data.get("has_more") or not page_token:
            return items


def find_child(lark, folder_token, name, types):
    for it in list_children(lark, folder_token):
        if it.get("name") == name and it.get("type") in types:
            return it
    return None


def ensure_folder(lark, parent, name):
    hit = find_child(lark, parent, name, ("folder",))
    if hit:
        print(f"[feishu] 使用已有文件夹「{name}」")
        return hit["token"], hit.get("url", "")
    args = ["drive", "+create-folder", "--name", name]
    if parent:
        args += ["--folder-token", parent]
    data = lark.data(args, f"创建文件夹 {name}")
    print(f"[feishu] 已创建文件夹「{name}」")
    return data["folder_token"], data.get("url", "")


def resolve_folder(lark, spec):
    """spec：空 = 根目录/jira-daily-board；文件夹链接；或 a/b/c 路径（逐级查找或创建）"""
    spec = (spec or "").strip()
    m = FOLDER_URL_RE.search(spec)
    if m:
        return m.group(1), spec
    parts = [p.strip() for p in spec.strip("/").split("/") if p.strip()] if spec else [DEFAULT_FOLDER]
    if parts and parts[0] in ("我的空间", "My Space"):
        parts = parts[1:]
    token, url = "", ""
    for name in parts:
        token, url = ensure_folder(lark, token, name)
    return token, url


# ─── 多维表格 ───────────────────────────────────────────────────────

def ensure_base(lark, folder_token, name):
    hit = find_child(lark, folder_token, name, ("bitable",))
    if hit:
        print(f"[feishu] 使用已有多维表格「{name}」")
        return hit["token"], hit.get("url", ""), False
    args = ["base", "+base-create", "--name", name, "--time-zone", "Asia/Shanghai",
            "--table-name", DETAIL_TABLE, "--fields", _json(DETAIL_SCHEMA)]
    if folder_token:
        args += ["--folder-token", folder_token]
    data = lark.data(args, "创建多维表格")
    base = data.get("base") or {}
    token = _first(base, "base_token", "app_token")
    if not token:
        raise LarkError(f"创建多维表格成功但没有返回 base_token: {_json(data)[:300]}")
    print(f"[feishu] 已创建多维表格「{name}」")
    return token, _first(base, "url"), True


def list_tables(lark):
    data = lark.data(["base", "+table-list", "--base-token", lark.base], "读取数据表")
    return {t.get("name"): _first(t, "id", "table_id") for t in data.get("tables") or []}


def ensure_table(lark, tables, name, schema):
    if tables.get(name):
        ensure_fields(lark, tables[name], name, schema)
        return tables[name], False
    data = lark.data(["base", "+table-create", "--base-token", lark.base, "--name", name,
                      "--fields", _json(schema)], f"创建数据表 {name}")
    tid = _first(data.get("table") or {}, "id", "table_id")
    if not tid:
        raise LarkError(f"创建数据表 {name} 成功但没有返回 table_id")
    print(f"[feishu] 已创建数据表「{name}」")
    return tid, True


def ensure_fields(lark, table_id, table_name, schema):
    """已有的表补上缺少的字段（老版本建的表、或手工建的表）"""
    try:
        have = lark.field_names(table_id)
    except LarkError as e:
        print(f"[feishu] [warn] 读取「{table_name}」字段失败，跳过补字段: {e}")
        return
    missing = [f for f in schema if f["name"] not in have]
    if not missing:
        return
    try:
        lark.run(["base", "+field-create", "--base-token", lark.base, "--table-id", table_id,
                  "--json", _json(missing)], f"补充字段 {table_name}")
        print(f"[feishu] 「{table_name}」已补充字段：{'、'.join(f['name'] for f in missing)}")
    except LarkError as e:
        print(f"[feishu] [warn] 「{table_name}」补充字段失败: {e}")


def _hide_fingerprint(lark, base_args, view):
    """视图里隐藏「同步指纹」列（只是展示设置，失败不影响同步）"""
    visible = [f["name"] for f in DETAIL_SCHEMA if f["name"] != "同步指纹"]
    try:
        lark.run(["base", "+view-set-visible-fields"] + base_args + ["--view-id", view, "--json",
                  _json({"visible_fields": visible})], f"设置视图字段 {view}", require_ok=False)
    except LarkError:
        pass


def _legacy_filter(flt):
    """旧版 lark-cli 的筛选写法（is / isGreater …）。新写法失败时用它重试一次"""
    conds = []
    for c in flt["conditions"]:
        field, op, value = c[0], c[1], (c[2] if len(c) > 2 else None)
        if op == "intersects":
            conds += [[field, "is", v] for v in value] if len(value) > 1 else [[field, "is", value[0]]]
        else:
            conds.append([field, "is" if op == "==" else op, value])
    logic = "or" if any(c[1] == "intersects" and len(c[2]) > 1 for c in flt["conditions"]) else flt.get("logic", "and")
    return {"logic": logic, "conditions": conds}


def _try(lark, args, what, problems, payloads=None):
    """依次尝试 payloads（新写法、旧写法），全部失败时记录原因；返回是否成功"""
    last = None
    for extra in (payloads or [[]]):
        try:
            lark.run(args + extra, what, require_ok=False)
            return True
        except LarkError as e:
            last = e
    problems.append(f"{what}：{last}")
    return False


def ensure_views(lark, table_id, new_base=False):
    """创建缺少的视图（按名称判断）。返回 (已有+新建的视图名集合, 问题列表)"""
    problems = []
    try:
        views = lark.data(["base", "+view-list", "--base-token", lark.base, "--table-id", table_id],
                          "读取视图").get("views") or []
    except LarkError as e:
        problems.append(f"读取视图列表：{e}")
        views = []
    existing = {_first(v, "name", "view_name") for v in views}
    base_args = ["--base-token", lark.base, "--table-id", table_id]
    if DEFAULT_VIEW not in existing:
        if new_base and views:  # 新建的表：把默认表格视图改名为「问题总表」
            ok = _try(lark, ["base", "+view-rename"] + base_args +
                      ["--view-id", _first(views[0], "id", "view_id"), "--name", DEFAULT_VIEW],
                      f"重命名默认视图为 {DEFAULT_VIEW}", problems)
        else:
            ok = _try(lark, ["base", "+view-create"] + base_args +
                      ["--json", _json({"name": DEFAULT_VIEW, "type": "grid"})], f"创建视图 {DEFAULT_VIEW}", problems)
        if ok:
            existing.add(DEFAULT_VIEW)
            _hide_fingerprint(lark, base_args, DEFAULT_VIEW)
            print(f"[feishu] 已创建视图「{DEFAULT_VIEW}」")
    for name, flt, group, sort in DETAIL_VIEWS:
        if name in existing:
            continue
        if not _try(lark, ["base", "+view-create"] + base_args + ["--json", _json({"name": name, "type": "grid"})],
                    f"创建视图 {name}", problems):
            continue
        existing.add(name)
        view_args = base_args + ["--view-id", name]
        if flt:
            _try(lark, ["base", "+view-set-filter"] + view_args, f"设置视图筛选 {name}", problems,
                 [["--json", _json(flt)], ["--json", _json(_legacy_filter(flt))]])
        if group:
            _try(lark, ["base", "+view-set-group"] + view_args, f"设置视图分组 {name}", problems,
                 [["--json", _json({"group_config": [{"field": group, "desc": False}]})]])
        if sort:
            _try(lark, ["base", "+view-set-sort"] + view_args, f"设置视图排序 {name}", problems,
                 [["--json", _json({"sort_config": [{"field": f, "desc": d} for f, d in sort]})]])
        _hide_fingerprint(lark, base_args, name)
        print(f"[feishu] 已创建视图「{name}」")
    return existing, problems


def _items(data, *keys):
    for k in keys:
        if isinstance(data.get(k), list):
            return data[k]
    return []


TYPE_FALLBACK = {"bar": "column", "ring": "pie"}  # 旧版 lark-cli 不认识时退回的图表类型


def _list_blocks(lark, did):
    for extra in (["--page-size", "100"], []):
        try:
            data = lark.data(["base", "+dashboard-block-list", "--base-token", lark.base,
                              "--dashboard-id", did] + extra, "读取仪表盘组件")
            return {_first(b, "name", "block_name") for b in _items(data, "items", "blocks")}
        except LarkError:
            continue
    return None


def ensure_dashboard(lark):
    """创建仪表盘；已存在时只补上缺少的组件（按组件名判断），不动已有组件和布局。
    返回 (已有+新建的组件名集合, 问题列表)"""
    problems, have = [], set()
    try:
        boards = lark.data(["base", "+dashboard-list", "--base-token", lark.base], "读取仪表盘")
        did = ""
        for b in _items(boards, "dashboards", "items"):
            if _first(b, "name", "dashboard_name") == DASHBOARD:
                did = _first(b, "dashboard_id", "block_id", "id")
        created_board, fallback_board = False, False
        if did:
            listed = _list_blocks(lark, did)
            if listed is None:
                # 读不到已有组件就无法判断缺哪些；为避免重复，另建一个完整的仪表盘
                problems.append(f"读取「{DASHBOARD}」的组件失败，已另建「{DASHBOARD}（完整）」")
                did, fallback_board = "", True
            else:
                have = listed
        if not did:
            name = f"{DASHBOARD}（完整）" if fallback_board else DASHBOARD
            data = lark.data(["base", "+dashboard-create", "--base-token", lark.base, "--name", name], "创建仪表盘")
            did = _first(data.get("dashboard") or {}, "dashboard_id", "block_id", "id")
            if not did:
                raise LarkError("创建仪表盘成功但没有返回 dashboard_id")
            created_board = True
        todo = [b for b in DASHBOARD_BLOCKS if b[0] not in have]
        for name, typ, conf in todo:  # 必须串行创建
            types = [typ] + ([TYPE_FALLBACK[typ]] if typ in TYPE_FALLBACK else [])
            if _try(lark, ["base", "+dashboard-block-create", "--base-token", lark.base, "--dashboard-id", did,
                           "--name", name, "--data-config", _json(conf)], f"创建组件 {name}", problems,
                    [["--type", t] for t in types]):
                have.add(name)
        if created_board and todo:  # 只有新建的仪表盘才自动整理布局
            _try(lark, ["base", "+dashboard-arrange", "--base-token", lark.base, "--dashboard-id", did],
                 "整理仪表盘布局", problems)
    except LarkError as e:
        problems.append(f"仪表盘：{e}")
    return have, problems


def print_report(views, dash, problems):
    want_views = [DEFAULT_VIEW] + [v[0] for v in DETAIL_VIEWS]
    want_blocks = [b[0] for b in DASHBOARD_BLOCKS]
    miss_v = [v for v in want_views if v not in views]
    miss_b = [b for b in want_blocks if b not in dash]
    print(f"\n[feishu] 检查结果：问题明细视图 {len(want_views) - len(miss_v)}/{len(want_views)}，"
          f"仪表盘组件 {len(want_blocks) - len(miss_b)}/{len(want_blocks)}")
    if miss_v:
        print(f"  缺少视图：{'、'.join(miss_v)}")
    if miss_b:
        print(f"  缺少组件：{'、'.join(miss_b)}")
    for p in problems:
        print(f"  [问题] {p[:300]}")
    if miss_v or miss_b:
        print("__FEISHU_INCOMPLETE__")


def setup(cfg, folder_spec="", base_name=DEFAULT_BASE_NAME, existing_base=""):
    """创建（或复用）多维表格，返回需要写回配置的字段 dict。失败抛 LarkError。

    existing_base：直接补全这个已有的多维表格（不新建、不找文件夹），缺的表/字段/视图/仪表盘组件会补上。"""
    lark = Lark(cfg)
    if existing_base:
        folder_token, folder_url = cfg.get("feishu_folder", ""), cfg.get("feishu_folder_url", "")
        base_token, base_url, new_base = existing_base, cfg.get("feishu_base_url", ""), False
    else:
        folder_token, folder_url = resolve_folder(lark, folder_spec)
        base_token, base_url, new_base = ensure_base(lark, folder_token, base_name)
    lark.base = base_token
    tables = list_tables(lark)
    detail_id, _ = ensure_table(lark, tables, DETAIL_TABLE, DETAIL_SCHEMA)
    snapshot_id, _ = ensure_table(lark, tables, SNAPSHOT_TABLE, SNAPSHOT_SCHEMA)
    views, p1 = ensure_views(lark, detail_id, new_base)
    dash, p2 = ensure_dashboard(lark)
    print_report(views, dash, p1 + p2)
    out = {
        "base_token": base_token, "detail_table": detail_id, "snapshot_table": snapshot_id,
        "feishu_folder": folder_token, "feishu_folder_url": folder_url,
        # 视图筛选已是相对日期，不需要每天刷新
        "view_today_updated": "", "view_today_created": "",
    }
    if base_url:
        out["feishu_base_url"] = base_url
        host = re.match(r"https?://[^/]+", base_url)
        if host:
            out["feishu_host"] = host.group(0)
    return out


if __name__ == "__main__":
    import config_manager as cm
    print(setup(cm.load(), sys.argv[1] if len(sys.argv) > 1 else ""))
