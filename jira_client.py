#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jira REST 取数：授权、分页拉取问题、批量拉评论、时间窗 JQL。

daily_board.py / fetch_jira.py / setup_wizard.py 共用，FIELDS 只在这里维护一份。
"""
import base64, datetime as dt, json, os, re, time, urllib.error, urllib.request

DEFAULT_BASE = ""  # 不内置任何 Jira 地址；由配置 jira_base（或筛选链接）提供

FIELDS = [
    "key", "id", "created", "updated", "status", "assignee", "reporter", "creator",
    "summary", "priority", "resolutiondate", "fixVersions", "labels",
    "customfield_10266",   # 负责人
    "customfield_10613",   # 负责人(文本)
    "customfield_10217",   # 严重度 A/B+/B/C
    "customfield_18913",   # 严重程度
    "customfield_10700",   # 功能模块
    "customfield_10301",   # 问题模块
    "customfield_11000",   # 功能测试域
    "customfield_18903",   # 工作组
    "customfield_16409",   # 系统测试方
    "customfield_10220",   # 缺陷来源
    "customfield_10227",   # 归属部门
    "customfield_10500",   # 控制器
    "customfield_10609",   # 用例等级
    "customfield_16402",   # 项目阶段
    "customfield_18941",   # 整车节点
    "customfield_16801",   # 转分析中日期
    "customfield_16802",   # 回分析中日期
    "customfield_16803",   # 转修复中日期
    "customfield_16804",   # 转验收中日期
    "customfield_16805",   # 转挂起日期
    "customfield_16809",   # 转跟踪中日期
    "customfield_16810",   # 转审核中日期
    "customfield_16808",   # 转完成日期
    "customfield_10600",   # 预计修复完成日期
    "customfield_11923",   # 最新状态更新时间
    "customfield_10211",   # 根因分析
    "customfield_10244",   # 解决方案
    "customfield_10201",   # 发现时间
    "customfield_10209",   # 发生软件版本
]

# 问题明细表里的扩展列 → Jira 字段。已知 ID 直接用；未知的按 Jira 字段名（含别名）自动查找，
# 也可以在配置 extra_fields 里写死，如 {"缺陷类型": "customfield_12345"}
EXTRA_FIELDS = {
    "问题来源": ["问题来源", "缺陷来源"],
    "缺陷类型": ["缺陷类型", "问题类型", "缺陷分类"],
    "问题原因大类": ["问题原因大类", "原因大类", "问题原因分类"],
}
KNOWN_EXTRA_IDS = {"问题来源": "customfield_10220"}

PAGE_SIZE = 500
COMMENT_BATCH = 80


class JiraError(RuntimeError):
    """Jira 错误（JQL 错误、网络失败等）"""


class JiraAuthError(JiraError):
    """401/403：授权无效或已过期，需要用户重新授权"""


# ─── 授权 ───────────────────────────────────────────────────────────

def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode()


def auth_candidates(cfg, decrypt_creds=None):
    """返回按优先级排列的 Authorization 头候选。

    Jira Server/DC 的 Personal Access Token 规范用法是 Bearer；
    保留旧的 Basic pat:<token> 作为兜底，兼容已跑通的环境。
    """
    method = cfg.get("auth_method", "")
    if method == "pat":
        token = cfg.get("pat_token", "")
        if not token:
            return []
        cands = ["Bearer " + token, basic("pat", token)]
        # setup_wizard 验证时记下了实际可用的格式，优先用它
        return cands[::-1] if cfg.get("pat_scheme") == "Basic" else cands
    if method == "credentials":
        user, pwd = decrypt_creds(cfg) if decrypt_creds else ("", "")
        return [basic(user, pwd)] if user and pwd else []
    return []


def _request(base, path, auth, payload=None, timeout=180):
    url = base.rstrip("/") + path
    headers = {"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    return urllib.request.Request(url, data=data, headers=headers)


def api(base, path, auth, payload=None, timeout=180, retries=4):
    """调用 Jira REST。401/403 立即抛 JiraAuthError，400 立即抛 JiraError（不重试），
    只有网络错误 / 429 / 5xx 才重试。"""
    req = _request(base, path, auth, payload, timeout)
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise JiraAuthError(f"Jira 授权无效或已过期（HTTP {e.code}）") from e
            if e.code == 400:
                raise JiraError("JQL 或请求错误: " + _error_text(e)) from e
            if e.code != 429 and e.code < 500:
                raise JiraError(f"HTTP {e.code} {path}: {_error_text(e)}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        if attempt < retries - 1:
            time.sleep(2 + attempt * 3)
    raise JiraError(f"API 失败 {path}: {last}")


def _error_text(e):
    try:
        body = json.loads(e.read().decode("utf-8"))
        msgs = body.get("errorMessages") or []
        msgs += [f"{k}: {v}" for k, v in (body.get("errors") or {}).items()]
        return "; ".join(msgs) or str(e)
    except Exception:
        return str(e)


def login(base, candidates):
    """依次尝试候选授权头，返回 (auth, myself)。全部 401/403 则抛 JiraAuthError。"""
    if not candidates:
        raise JiraAuthError("未配置可用的 Jira 凭证")
    err = None
    for auth in candidates:
        try:
            return auth, api(base, "/rest/api/2/myself", auth, timeout=30, retries=2)
        except JiraAuthError as e:
            err = e
    raise err


# ─── JQL ────────────────────────────────────────────────────────────

ORDER_BY_RE = re.compile(r"\border\s+by\b", re.I)


def windowed_jql(jql, weeks, today=None):
    """给 JQL 加 created 时间窗。

    原 JQL 用括号包起来（避免 OR 优先级问题），ORDER BY 挪到最后（避免语法错误）。
    weeks <= 0 表示不加时间窗。返回 (jql, start_date 或 None)。
    """
    jql = (jql or "").strip()
    if not weeks or weeks <= 0:
        return jql, None
    today = today or dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    start = monday - dt.timedelta(days=(weeks - 1) * 7)
    order = ""
    matches = list(ORDER_BY_RE.finditer(jql))
    if matches:
        m = matches[-1]
        jql, order = jql[:m.start()].strip(), " " + jql[m.start():].strip()
    cond = f"created >= '{start.isoformat()}'"
    jql = f"({jql}) AND {cond}" if jql else cond
    return jql + order, start


# ─── 取数 ───────────────────────────────────────────────────────────

def resolve_extra_fields(base, auth, overrides=None):
    """返回 {扩展列名: Jira 字段 ID}；查不到的列不出现在结果里"""
    ids = dict(KNOWN_EXTRA_IDS)
    ids.update({k: v for k, v in (overrides or {}).items() if v})
    missing = [c for c in EXTRA_FIELDS if not ids.get(c)]
    if missing:
        try:
            by_name = {f.get("name"): f.get("id") for f in api(base, "/rest/api/2/field", auth, retries=2)}
        except JiraAuthError:
            raise
        except JiraError as e:
            print(f"[jira] [warn] 读取 Jira 字段列表失败，扩展列 {missing} 留空: {e}")
            by_name = {}
        for col in missing:
            for alias in EXTRA_FIELDS[col]:
                if by_name.get(alias):
                    ids[col] = by_name[alias]
                    break
    return {c: ids[c] for c in EXTRA_FIELDS if ids.get(c)}


def count_issues(base, auth, jql):
    """只查命中条数，用于配置时校验 JQL；JQL 错误抛 JiraError"""
    d = api(base, "/rest/api/2/search", auth, {"jql": jql, "maxResults": 0, "fields": ["key"]}, retries=2)
    if d.get("errorMessages"):
        raise JiraError("JQL 错误: " + "; ".join(d["errorMessages"]))
    return d.get("total", 0)


def fetch_issues(base, auth, jql, page=PAGE_SIZE, limit=None, verbose=True, extra_ids=None):
    out, start, total = [], 0, None
    fields = FIELDS + [f for f in (extra_ids or {}).values() if f not in FIELDS]
    while True:
        body = {"jql": jql, "maxResults": page, "startAt": start, "fields": fields}
        d = api(base, "/rest/api/2/search", auth, body)
        if d.get("errorMessages") and not out:
            raise JiraError("JQL 错误: " + "; ".join(d["errorMessages"]))
        if total is None:
            total = d.get("total", 0)
            if verbose:
                print(f"[jira] 命中 {total} 条，开始分页拉取", flush=True)
        issues = d.get("issues", [])
        if not issues:
            break
        out.extend(issues)
        start += len(issues)
        if verbose:
            print(f"  ... {start}/{total}", flush=True)
        if (limit and start >= limit) or start >= total:
            break
    return out, total


def comment_keys(issues, scope="open"):
    """open = 未完成 + 近 7 天新增；all = 全部；none = 不抓"""
    if scope == "none":
        return []
    if scope == "all":
        return [it["key"] for it in issues]
    since = time.strftime("%Y-%m-%dT", time.localtime(time.time() - 7 * 86400))
    sel = []
    for it in issues:
        f = it["fields"]
        st = (f.get("status") or {}).get("name", "")
        if st != "完成" or (f.get("created") or "") >= since:
            sel.append(it["key"])
    return sel


def fetch_comments(base, auth, keys, batch=COMMENT_BATCH, verbose=True):
    """走 search + `issuekey in (...)` 批量取评论，避免逐单请求。授权失败直接抛出。"""
    res = {}
    for i in range(0, len(keys), batch):
        chunk = keys[i:i + batch]
        body = {"jql": "issuekey in (%s)" % ",".join(chunk), "maxResults": batch, "fields": ["key", "comment"]}
        try:
            d = api(base, "/rest/api/2/search", auth, body)
        except JiraAuthError:
            raise
        except Exception as e:
            if verbose:
                print(f"  [warn] 评论批次失败 {i}: {e}", flush=True)
            continue
        for it in d.get("issues", []):
            cs = (it["fields"].get("comment") or {}).get("comments") or []
            res[it["key"]] = [{"author": (c.get("author") or {}).get("displayName", ""),
                               "created": c.get("created", ""),
                               "body": (c.get("body") or "").strip()} for c in cs]
        if verbose:
            print(f"  [comment] {len(res)}/{len(keys)}", flush=True)
    return res


# ─── 落盘 ───────────────────────────────────────────────────────────

def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def write_issues(path, base, jql, total, issues, fetched_at=None, extra_ids=None):
    write_json(path, {"base_url": base, "jql": jql, "total": total,
                      "fetched_at": fetched_at or time.strftime("%Y-%m-%d %H:%M:%S"),
                      "extra_field_ids": extra_ids or {},
                      "count": len(issues), "issues": issues})
