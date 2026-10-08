#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""口径计算：Jira 原始 issue → 标准化记录 → 汇总统计。

飞书快照表和文本摘要都用这里的 compute_stats，保证两边数字一致。
口径见 SKILL.md「闭环状态映射」「时效口径」。
"""
import datetime as dt, re

STAGE_MAP = {
    "待办": "0/4", "已分配": "0/4", "分析中": "1/4", "修复中": "2/4",
    "验收中": "3/4", "审核中": "3/4", "完成": "4/4",
    "挂起": "挂起", "申请挂起中": "挂起", "跟踪中": "跟踪中",
}
UNCLOSED_STAGES = ("0/4", "1/4", "2/4")
SOLVED_STAGES = ("3/4", "4/4")

# 时效阈值（天，严格大于）
NEW_WARN_DAYS = 1       # 0/4：> 1 天 预警
NEW_TIMEOUT_DAYS = 3    # 0/4：> 3 天 严重超时
STAGE_WARN_DAYS = 7     # 1/4~3/4：> 7 天 预警
STAGE_TIMEOUT_DAYS = 12 # 1/4~3/4：> 12 天 超时

DATE_FIELDS = {
    "转分析中": "customfield_16801", "回分析中": "customfield_16802",
    "转修复中": "customfield_16803", "转验收中": "customfield_16804",
    "转挂起": "customfield_16805", "转跟踪中": "customfield_16809",
    "转审核中": "customfield_16810", "转完成": "customfield_16808",
}
STAGE_ANCHORS = {"1/4": ["转分析中"], "2/4": ["转分析中", "转修复中"],
                 "3/4": ["转分析中", "转修复中", "转验收中", "转审核中"]}

NAME_RE = re.compile(r"\s+")


def clean_name(s):
    return NAME_RE.sub("", str(s or "")).strip()


def val(f, k):
    v = f.get(k)
    if isinstance(v, dict):
        return v.get("value") or v.get("displayName") or v.get("name")
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return "、".join(str(x.get("value") or x.get("name") or "") for x in v)
    return v


def d(part):
    return (part or "")[:10]


def iso_week(day):
    return "CW%02d" % dt.date.fromisoformat(day).isocalendar()[1]


def days_between(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def plain(s, n=260):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    s = re.sub(r"\[[^]]*\]\(https?://[^)]+\)", "", s)
    s = re.sub(r"!?\[[^\]]*\]", "", s)
    s = re.sub(r"![^!\n]{2,140}!", "", s)
    s = re.sub(r"^[\s\W]{0,3}(image|clipboard)[^\s]*", "", s)
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", "", s)
    return s[:n] + ("…" if len(s) > n else "")


def aging(rec, now):
    """返回 (时效标签, 停留天数)"""
    stage = rec["stage"]
    if stage == "4/4":
        return "正常", 0
    if stage not in ("0/4",) + tuple(STAGE_ANCHORS):
        # 挂起 / 跟踪中 / 其他 / 未知：不计时效
        return stage, max(0, days_between(rec["created"], now)) if rec["created"] else 0
    if stage == "0/4":
        anchor = rec["created"]
    else:
        cand = [rec["dates"][p] for p in STAGE_ANCHORS[stage] if rec["dates"].get(p)] or [rec["created"]]
        anchor = max(cand)
    n = max(0, days_between(anchor, now)) if anchor else 0
    if stage == "0/4":
        if n > NEW_TIMEOUT_DAYS: return "严重超时", n
        if n > NEW_WARN_DAYS: return "预警", n
        return "正常", n
    if n > STAGE_TIMEOUT_DAYS: return "超时", n
    if n > STAGE_WARN_DAYS: return "预警", n
    return "正常", n


def solved_date(stage, dates, resolution):
    """首次到达 3/4+ 的日期；当前不在 3/4 / 4/4（例如被打回）则视为未解决"""
    if stage not in SOLVED_STAGES:
        return ""
    cand = [x for x in (dates.get("转验收中"), dates.get("转审核中"), dates.get("转完成"), resolution) if x]
    return min(cand) if cand else ""


def latest_progress(comments, fields):
    if comments:
        c = comments[-1]
        last_c = d(c.get("created"))
        stamp = "%d/%d" % (int(last_c[5:7]), int(last_c[8:10])) if last_c else ""
        progress = ("%s %s：%s" % (stamp, clean_name(c.get("author")), plain(c.get("body")))).strip(" ：")
        if progress:
            return progress
    return plain(val(fields, "customfield_10244") or val(fields, "customfield_10211") or "", 200)


def extra_text(v):
    v = val({"x": v}, "x") if isinstance(v, (dict, list)) else v
    return re.sub(r"\s+", " ", str(v)).strip() if v not in (None, "") else ""


def normalize(doc, comments, now):
    out = []
    extra_ids = doc.get("extra_field_ids") or {}
    for it in doc["issues"]:
        f = it["fields"]
        status = (f.get("status") or {}).get("name", "")
        stage = STAGE_MAP.get(status, "其他" if status else "未知")
        dates = {k: d(val(f, cf)) for k, cf in DATE_FIELDS.items()}
        created = d(f.get("created"))
        resolution = d(f.get("resolutiondate"))
        rec = {
            "key": it["key"], "created": created,
            "solved": solved_date(stage, dates, resolution),
            "updated": f.get("updated", ""),
            "status": status, "stage": stage,
            "sev": val(f, "customfield_10217") or val(f, "customfield_18913") or "-",
            "mod": val(f, "customfield_10700") or val(f, "customfield_10301") or "",
            "eng": clean_name((f.get("assignee") or {}).get("displayName")) or "未分配",
            "summary": (f.get("summary") or "").strip(),
            "progress": latest_progress(comments.get(it["key"]) or [], f),
            "dates": dates,
            "week": iso_week(created) if created else "",
            "extra": {col: extra_text(f.get(fid)) for col, fid in extra_ids.items()},
        }
        rec["ag"], rec["agDays"] = aging(rec, now)
        out.append(rec)
    return out


def compute_stats(recs, now):
    today = dt.date.fromisoformat(now)
    monday = (today - dt.timedelta(days=today.weekday())).isoformat()
    stage, sev, week = {}, {}, {}
    for r in recs:
        stage[r["stage"]] = stage.get(r["stage"], 0) + 1
        sev[r["sev"]] = sev.get(r["sev"], 0) + 1
        if r["week"]:
            week[r["week"]] = week.get(r["week"], 0) + 1
    return {
        "date": now,
        "total": len(recs),
        "today_new": sum(1 for r in recs if r["created"] == now),
        "today_done": sum(1 for r in recs if r["solved"] == now),
        "week_new": sum(1 for r in recs if r["created"] and r["created"] >= monday),
        "week_done": sum(1 for r in recs if r["solved"] and r["solved"] >= monday),
        "timeout": sum(1 for r in recs if r["ag"] in ("超时", "严重超时")),
        "warn": sum(1 for r in recs if r["ag"] == "预警"),
        "unclosed": sum(1 for r in recs if r["stage"] in UNCLOSED_STAGES),
        "parked": stage.get("挂起", 0) + stage.get("跟踪中", 0),
        "by_stage": stage, "by_sev": sev, "by_week": week,
    }
