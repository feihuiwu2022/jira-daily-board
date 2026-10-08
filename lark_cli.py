#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lark-cli 调用封装（user 身份）。sync_to_base.py / feishu_setup.py 共用。"""
import json, os, shutil, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "_work")

BATCH_SIZE = 200


class LarkError(RuntimeError):
    pass


class Lark:
    def __init__(self, cfg):
        # 路径和 profile 只来自环境变量、本机配置或 site_defaults.json，代码里不内置任何安装路径
        configured = os.environ.get("LARK_CLI") or cfg.get("lark_cli")
        if configured and not (os.path.exists(configured) or shutil.which(configured)):
            configured = ""  # 配置的路径在本机不存在时退回 PATH
        self.cli = configured or shutil.which("lark-cli") or "lark-cli"
        self.profile = os.environ.get("LARK_PROFILE") or cfg.get("lark_profile") or ""
        self.base = cfg.get("base_token", "")

    def run(self, args, what="", require_ok=True):
        """rc!=0 或返回 ok=false 时抛 LarkError。
        require_ok=True 时还要求返回 {"ok": true, ...} 的 JSON（读/写记录都应如此）。"""
        cmd = [self.cli] + (["--profile", self.profile] if self.profile else []) + args + ["--as", "user"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=HERE)
        except FileNotFoundError:
            raise LarkError(f"找不到 lark-cli：{self.cli}（可在配置 lark_cli 或环境变量 LARK_CLI 指定）")
        if r.returncode:
            raise LarkError(f"{what or args[1]} 失败 rc={r.returncode}: {(r.stderr or r.stdout)[:500]}")
        try:
            res = json.loads(r.stdout)
        except ValueError:
            res = r.stdout
        if isinstance(res, dict) and (res.get("ok") is False or res.get("code") not in (None, 0)):
            raise LarkError(f"{what or args[1]} 失败: {json.dumps(res.get('error') or res, ensure_ascii=False)[:300]}")
        # shortcut 命令返回 {"ok":true,"data":…}；原生 API 命令返回 {"code":0,"data":…}
        if require_ok and not (isinstance(res, dict) and (res.get("ok") or res.get("code") == 0)):
            raise LarkError(f"{what or args[1]} 返回异常: {str(res)[:300]}")
        return res

    def data(self, args, what=""):
        """执行并返回 data 部分（dict）"""
        res = self.run(args, what)
        return res.get("data") or {}

    def field_names(self, table_id):
        data = self.data(["base", "+field-list", "--base-token", self.base, "--table-id", table_id], "读取字段")
        return {f.get("name") or f.get("field_name") for f in data.get("fields") or []}

    def run_with_payload(self, args, payload, what=""):
        os.makedirs(WORK, exist_ok=True)
        rel = os.path.join("_work", f"_payload_{os.getpid()}.json")
        path = os.path.join(HERE, rel)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        try:
            return self.run(args + ["--json", f"@{rel}"], what)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def list_records(self, table_id, fields=None):
        """分页读取记录，返回 [(record_id, {字段名: 值})]。fields 只读取指定字段（数据量小、速度快）"""
        out, seen, offset = [], set(), 0
        field_args = [x for f in (fields or []) for x in ("--field-id", f)]
        while True:
            res = self.run(["base", "+record-list", "--base-token", self.base, "--table-id", table_id,
                            "--limit", str(BATCH_SIZE), "--offset", str(offset), "--format", "json"] + field_args,
                           "读取记录")
            data = res.get("data", {}) if isinstance(res, dict) else {}
            ids = data.get("record_id_list", []) or []
            rows = data.get("data") or []
            names = data.get("fields") or data.get("field_names") or fields or []
            names = [n.get("name") if isinstance(n, dict) else n for n in names]
            page = []
            for i, rid in enumerate(ids):
                if rid in seen:
                    continue
                row = rows[i] if i < len(rows) else None
                vals = dict(zip(names, row)) if isinstance(row, list) else (row if isinstance(row, dict) else {})
                page.append((rid, vals))
            if not page:
                break
            out.extend(page)
            seen.update(rid for rid, _ in page)
            if not data.get("has_more", False):
                break
            offset += BATCH_SIZE
        return out

    def list_record_ids(self, table_id):
        return [rid for rid, _ in self.list_records(table_id, fields=["编号"])]

    def delete_records(self, table_id, record_ids, label=""):
        for i in range(0, len(record_ids), BATCH_SIZE):
            args = ["base", "+record-delete", "--base-token", self.base, "--table-id", table_id, "--yes"]
            for rid in record_ids[i:i + BATCH_SIZE]:
                args += ["--record-id", rid]
            self.run(args, "删除记录", require_ok=False)
            print(f"  [{label}] 已删除 {min(i + BATCH_SIZE, len(record_ids))}/{len(record_ids)}")

    def create_rows(self, table_id, fields, rows, label=""):
        for i in range(0, len(rows), BATCH_SIZE):
            chunk = rows[i:i + BATCH_SIZE]
            self.run_with_payload(["base", "+record-batch-create", "--base-token", self.base,
                                   "--table-id", table_id], {"fields": fields, "rows": chunk},
                                  f"写入批次 {i // BATCH_SIZE + 1}")
            print(f"  [{label}] {i + len(chunk)}/{len(rows)}")
            time.sleep(0.3)
