#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看板配置管理：读写 ~/.jira_board_config.json

配置项：
  auth_method    : browser | pat | credentials   （必选）
  jira_base      : Jira 地址（必填；配置时从筛选链接自动提取）
  pat_token      : PAT token（auth_method=pat 时）
  jql            : JQL 查询语句（必选）
  base_token     : 飞书多维表格 Token（必选）
  detail_table   : 问题明细表 ID（--create-base 自动填写）
  snapshot_table : 日清快照表 ID（--create-base 自动填写）
  mail_to        : 邮件收件人（可选，空=不发送）
  schedule_time  : 定时时间 HH:MM（可选，默认 17:00，空=不定时）
  title          : 看板标题（可选）
  weeks          : 回溯周数（可选，默认 9）
  sender_email   : 发件人邮箱（可选）
  lark_cli       : lark-cli 可执行文件路径（可选，默认用 PATH 中的 lark-cli）
  lark_profile   : lark-cli profile（可选，默认不指定）
  view_today_updated / view_today_created : 「今日更新/今日新增」视图 ID（可选，空=不刷新视图）
"""
import base64, hashlib, json, os, sys

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".jira_board_config.json")

DEFAULTS = {
    "version": 2,
    "auth_method": "",
    "jira_base": "",
    "pat_token": "",
    "jql": "",
    "base_token": "",
    "feishu_host": "https://feishu.cn",
    "detail_table": "",
    "snapshot_table": "",
    "mail_to": "",
    "schedule_time": "17:00",
    "title": "问题日清看板",
    "weeks": 9,
    "sender_email": "",
    "lark_cli": "",
    "lark_profile": "",
    "view_today_updated": "",
    "view_today_created": "",
}

WEEK_OPTIONS = {
    "2": 2,
    "4": 4,
    "9": 9,
    "13": 13,
    "26": 26,
    "all": 0,
    "全部": 0,
}


# 站点默认值：和脚本放在同一目录的 site_defaults.json（企业内部部署时提供 Jira 地址、飞书域名、
# lark-cli 路径/profile 等）。公开仓库只带 site_defaults.example.json，真实文件已在 .gitignore 中排除。
SITE_DEFAULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_defaults.json")
SITE_KEYS = ("jira_base", "feishu_host", "lark_cli", "lark_profile", "extra_fields")


def _apply_site_defaults():
    if not os.path.exists(SITE_DEFAULTS_PATH):
        return
    try:
        site = json.load(open(SITE_DEFAULTS_PATH, encoding="utf-8"))
    except Exception as e:
        print(f"[config] 读取 site_defaults.json 失败: {e}", file=sys.stderr)
        return
    DEFAULTS.update({k: v for k, v in site.items() if k in SITE_KEYS and v})


_apply_site_defaults()


def load():
    """读取配置，返回 dict。文件不存在返回默认值（含站点默认值）。"""
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULTS)
    try:
        cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
        merged = dict(DEFAULTS)
        merged.update(cfg)
        merged["weeks"] = parse_weeks(merged.get("weeks", 9))
        return merged
    except Exception as e:
        print(f"[config] 读取配置失败: {e}，使用默认值", file=sys.stderr)
        return dict(DEFAULTS)


def save(cfg):
    """保存配置到 ~/.jira_board_config.json"""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    # Windows: 设置文件权限仅当前用户可读
    try:
        import stat
        os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    print(f"[config] 已保存到 {CONFIG_PATH}")


# ─── 凭证加解密 ─────────────────────────────────────────────────────
# Windows 上用 DPAPI（绑定当前 Windows 用户）；没有 pywin32 时退化为与机器名异或，
# 这只是混淆、不是加密，会在保存时给出提示。

def _fallback_key():
    return hashlib.sha256(os.environ.get("COMPUTERNAME", "default").encode()).digest()


def encrypt_creds(user, password):
    plaintext = f"{user}\n{password}".encode("utf-8")
    try:
        import win32crypt
        encrypted = win32crypt.CryptProtectData(plaintext, None, None, None, None, 0)
        return base64.b64encode(encrypted).decode()
    except ImportError:
        print("[config] 警告：未安装 pywin32，凭证只做了简单混淆（pip install pywin32 可启用 DPAPI 加密）",
              file=sys.stderr)
        key = _fallback_key()
        data = bytes(b ^ key[i % len(key)] for i, b in enumerate(plaintext))
        return "fallback:" + base64.b64encode(data).decode()


def decrypt_creds(cfg):
    """返回 (user, password)；解密失败抛 RuntimeError（不再悄悄返回空密码）"""
    encrypted = cfg.get("_encrypted_creds", "")
    if not encrypted:
        raise RuntimeError("未保存工号密码，请重新运行 setup_wizard.py --auth credentials")
    try:
        if encrypted.startswith("fallback:"):
            key = _fallback_key()
            data = base64.b64decode(encrypted[len("fallback:"):])
            decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        else:
            import win32crypt
            decrypted = win32crypt.CryptUnprotectData(base64.b64decode(encrypted), None, None, None, 0)[1]
        user, _, pwd = decrypted.decode("utf-8").partition("\n")
        return user, pwd
    except Exception as e:
        raise RuntimeError(f"凭证解密失败（换了电脑/Windows 用户？），请重新配置工号密码: {e}")


def validate(cfg):
    """校验必填项，返回 (ok, errors)"""
    errors = []
    if not cfg.get("auth_method"):
        errors.append("auth_method 未设置（browser / pat / credentials）")
    if cfg.get("auth_method") == "pat" and not cfg.get("pat_token"):
        errors.append("auth_method=pat 但 pat_token 为空")
    if cfg.get("auth_method") == "credentials" and not cfg.get("_encrypted_creds"):
        errors.append("auth_method=credentials 但未保存工号密码")
    if not cfg.get("jira_base"):
        errors.append("jira_base 为空（Jira 地址，可从筛选链接自动提取）")
    if not cfg.get("jql"):
        errors.append("jql 为空")
    if not cfg.get("base_token"):
        errors.append("base_token 为空（飞书多维表格 Token，用 --create-base 自动创建）")
    elif not (cfg.get("detail_table") and cfg.get("snapshot_table")):
        errors.append("detail_table / snapshot_table 为空（用 --create-base 或 --repair-base 自动填写）")
    return (len(errors) == 0, errors)


def is_configured():
    """检查是否已完成首次配置"""
    if not os.path.exists(CONFIG_PATH):
        return False
    cfg = load()
    ok, _ = validate(cfg)
    return ok


def parse_weeks(value):
    """解析周数：支持数字、'all'、'全部'"""
    if isinstance(value, int):
        return value
    s = str(value).strip().lower()
    if s in WEEK_OPTIONS:
        return WEEK_OPTIONS[s]
    try:
        return max(0, int(s))
    except ValueError:
        return 9


def extract_jql_from_url(url):
    """从 Jira URL 中提取 JQL。

    支持 ?jql=...（parse_qs 已经解码过一次，不能再 unquote，否则 JQL 里的 % 会被破坏），
    以及保存的筛选器链接 ?filter=12345 → `filter = 12345`。
    """
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url.strip())
    for part in (parsed.query, parsed.fragment):
        params = parse_qs(part)
        if params.get("jql") and params["jql"][0].strip():
            return params["jql"][0].strip()
    for part in (parsed.query, parsed.fragment):
        params = parse_qs(part)
        fid = (params.get("filter") or params.get("requestId") or [""])[0].strip()
        if fid.isdigit():
            return f"filter = {fid}"
    return ""


def extract_base_token_from_url(url):
    """从飞书多维表格 URL 中提取 base_token"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if "/base/" in path:
        return path.split("/base/")[-1].split("/")[0]
    return ""


def extract_jira_base(url):
    """从 Jira 筛选链接中提取站点地址，如 https://jira.example.com（带上下文路径时保留到 /issues 之前）"""
    from urllib.parse import urlparse
    p = urlparse(url.strip())
    if not (p.scheme and p.netloc):
        return ""
    path = p.path
    for marker in ("/issues", "/browse", "/secure", "/projects"):
        if marker in path:
            path = path[:path.index(marker)]
            break
    else:
        path = ""
    return f"{p.scheme}://{p.netloc}{path}".rstrip("/")


def extract_table_id(url):
    """从飞书多维表格 URL（…/base/TOKEN?table=tblXXX&view=…）中提取表 ID"""
    from urllib.parse import urlparse, parse_qs
    v = (parse_qs(urlparse(url.strip()).query).get("table") or [""])[0]
    return v if v.startswith("tbl") else ""


def extract_feishu_host(url):
    from urllib.parse import urlparse
    p = urlparse(url.strip())
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


def base_url(cfg):
    token = cfg.get("base_token", "")
    if token and cfg.get("feishu_base_url"):
        return cfg["feishu_base_url"]
    return f"{(cfg.get('feishu_host') or DEFAULTS['feishu_host']).rstrip('/')}/base/{token}" if token else "-"


def show_config(cfg):
    """格式化显示当前配置"""
    auth_display = {"browser": "浏览器登录", "pat": "PAT Token", "credentials": "工号+密码（加密存储）"}
    weeks_display = cfg.get("weeks", 9)
    if weeks_display == 0:
        weeks_display = "全部"
    else:
        weeks_display = f"{weeks_display} 周"


    lines = [
        "═══ 当前看板配置 ═══",
        f"  Jira 地址      : {cfg.get('jira_base', '-')}",
        f"  授权方式       : {auth_display.get(cfg.get('auth_method', ''), '未配置')}",
        f"  JQL            : {(cfg.get('jql', '') or '-')[:80]}{'...' if len(cfg.get('jql', '') or '') > 80 else ''}",
        f"  飞书多维表格   : {base_url(cfg)}",
        f"  飞书文件夹     : {cfg.get('feishu_folder_url') or '-'}",
        f"  问题明细表 ID  : {cfg.get('detail_table', '-')}",
        f"  日清快照表 ID  : {cfg.get('snapshot_table', '-')}",
        f"  看板标题       : {cfg.get('title', '-')}",
        f"  邮件收件人     : {cfg.get('mail_to', '-') or '不发送'}",
        f"  定时时间       : {cfg.get('schedule_time', '-') or '不定时'}",
        f"  回溯周数       : {weeks_display}",
        f"  发件人邮箱     : {cfg.get('sender_email', '-') or '自动检测'}",
        f"  lark-cli       : {cfg.get('lark_cli') or '自动检测'} (profile={cfg.get('lark_profile') or '默认'})",
        "═══════════════════",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    cfg = load()
    if is_configured():
        print(show_config(cfg))
    else:
        print("尚未配置，请先运行 setup_wizard.py")
