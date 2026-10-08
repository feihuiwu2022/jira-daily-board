// Jira 浏览器取数脚本：在已登录 Jira 的页面里用 browser evaluate 执行。
//
// 推荐用法：先运行 `python daily_board.py --emit-browser-script`，
// 它会把当天的 JQL（已加时间窗）和字段列表填进 CFG，生成 _work/jira_fetch_browser.generated.js，
// 助手 对那个文件的内容执行 browser evaluate，把返回的 JSON 原样写入 _work/browser_fetch_result.json。
//
// 返回值（同时挂在 window.__JIRA_FETCH_RESULT）：
//   { ok, error, base_url, jql, total, fetched_at, issues: [...], comments: {KEY: [...]} }
//   error 为 "AUTH_EXPIRED" 表示登录失效，需要让用户重新登录。
(async () => {
  const CFG = /*__CONFIG__*/ (window.__JIRA_FETCH_CONFIG || {});
  const PAGE = CFG.page || 500;
  const COMMENT_BATCH = CFG.commentBatch || 80;
  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (d, sep) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}${sep}`;
  const nowStr = () => {
    const d = new Date();
    return fmt(d, " ") + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };
  const result = { ok: false, error: "", base_url: location.origin, jql: CFG.jql || "",
                   total: 0, fetched_at: "", issues: [], comments: {} };

  async function call(path, body) {
    let last = "";
    for (let attempt = 0; attempt < 3; attempt++) {
      const r = await fetch(path, {
        method: body ? "POST" : "GET",
        credentials: "include",
        headers: { "Content-Type": "application/json", "Accept": "application/json",
                   "X-Atlassian-Token": "no-check" },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (r.status === 401 || r.status === 403) throw new Error("AUTH_EXPIRED");
      if (r.ok) return r.json();
      last = `HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`;
      if (r.status === 400) throw new Error("JQL_ERROR " + last);
      await new Promise((res) => setTimeout(res, 2000 * (attempt + 1)));
    }
    throw new Error(last);
  }

  try {
    if (!result.jql) throw new Error("NO_JQL");
    const me = await call("/rest/api/2/myself");
    if (!me || !me.name) throw new Error("AUTH_EXPIRED");

    // 扩展列：已知 ID 直接用，其余按 Jira 字段名（含别名）查找
    const extraIds = Object.assign({}, CFG.extraIds || {});
    const missing = Object.keys(CFG.extraFields || {}).filter((c) => !extraIds[c]);
    if (missing.length) {
      try {
        const byName = {};
        for (const f of await call("/rest/api/2/field")) byName[f.name] = f.id;
        for (const c of missing) {
          const hit = CFG.extraFields[c].find((n) => byName[n]);
          if (hit) extraIds[c] = byName[hit];
        }
      } catch (e) {
        if (String(e.message) === "AUTH_EXPIRED") throw e;
      }
    }
    result.extra_field_ids = extraIds;
    const fields = (CFG.fields || ["*navigable"]).concat(Object.values(extraIds));

    let start = 0, total = null;
    while (true) {
      const d = await call("/rest/api/2/search",
        { jql: result.jql, startAt: start, maxResults: PAGE, fields });
      if (total === null) total = d.total || 0;
      const batch = d.issues || [];
      if (!batch.length) break;
      result.issues.push(...batch);
      start += batch.length;
      if (start >= total) break;
    }
    result.total = total || 0;

    // 评论：未完成 + 近 7 天新增（与 jira_client.comment_keys 的 open 口径一致）
    const since = fmt(new Date(Date.now() - 7 * 86400000), "T");
    const keys = result.issues
      .filter((it) => ((it.fields.status || {}).name || "") !== "完成" || (it.fields.created || "") >= since)
      .map((it) => it.key);
    for (let i = 0; i < keys.length; i += COMMENT_BATCH) {
      const chunk = keys.slice(i, i + COMMENT_BATCH);
      try {
        const d = await call("/rest/api/2/search",
          { jql: `issuekey in (${chunk.join(",")})`, maxResults: COMMENT_BATCH, fields: ["key", "comment"] });
        for (const it of d.issues || []) {
          const cs = ((it.fields.comment || {}).comments) || [];
          result.comments[it.key] = cs.map((c) => ({
            author: (c.author || {}).displayName || "", created: c.created || "", body: (c.body || "").trim(),
          }));
        }
      } catch (e) {
        if (String(e.message) === "AUTH_EXPIRED") throw e;
      }
    }
    result.ok = true;
  } catch (e) {
    result.error = String((e && e.message) || e);
  }
  result.fetched_at = nowStr();
  window.__JIRA_FETCH_RESULT = result;
  return result;
})();
