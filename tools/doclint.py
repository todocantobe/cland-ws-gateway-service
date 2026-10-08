#!/usr/bin/env python3
"""doclint.py — pattern-hub 模式合规检查器 (L1 校验)

检查 Markdown 文档是否符合模式规范:
  ai-readable-doc  front-matter 头 (title/summary/read_when 必填, read_when 2-4 条)
  qa-doc           用户问答文档结构 (front-matter + 章节/交互形态表/FAQ/文档索引)

用法:
  python3 tools/doclint.py [目录] [--pattern all|ai-readable-doc|qa-doc] [--quiet]

退出码: 0 = 全部合规; 1 = 存在违规 (可挂 CI / pre-commit)
"""

import argparse
import os
import re
import sys

REQUIRED = ["title", "summary", "read_when"]
OPTIONAL = ["scope", "status", "updated"]
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "htmlcov",
    "cover",
}


def parse_header(lines):
    """解析 front-matter, 返回 (字段dict, 错误)。列表字段归入 <key>_items。"""
    if not lines or lines[0].strip() != "---":
        return None, "MISSING HEADER"
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, "UNCLOSED HEADER"
    d, cur = {}, None
    for ln in "".join(lines[1:end]).splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", ln)
        if m:
            cur = m.group(1)
            d.setdefault(cur, m.group(2).strip())
        elif cur and re.match(r"^\s+-\s+", ln):
            d.setdefault(cur + "_items", []).append(re.sub(r"^\s+-\s+", "", ln).strip())
    return d, None


def check_ai_readable_doc(lines):
    """ai-readable-doc 模式: front-matter 合规。返回 (errors, warns)。"""
    hdr, err = parse_header(lines)
    if err:
        return [err], []
    errors, warns = [], []
    for f in REQUIRED:
        if f not in hdr:
            errors.append(f"MISSING {f}")
    if "read_when" in hdr:
        n = len(hdr.get("read_when_items", []))
        if not (2 <= n <= 4):
            errors.append(f"read_when count={n} (期望 2-4 条)")
    for f in OPTIONAL:
        if f not in hdr:
            warns.append(f"optional missing: {f}")
    return errors, warns


def check_qa_doc(lines, path):
    """qa-doc 模式: 结构检查 (front-matter + 章节 + 交互形态表 + FAQ + 文档索引)。"""
    errors, warns = check_ai_readable_doc(lines)
    text = "".join(lines)
    headings = re.findall(r"^#{2,3}\s+(.+)$", text, re.M)
    if len(headings) < 6:
        errors.append(f"章节不足 (## 标题 {len(headings)} 个, 期望 >= 6)")
    if not re.search(r"触发|交互", text):
        warns.append("缺少交互形态描述 (建议含'触发/交互'字样)")
    if not re.search(r"^\|.*\|.*\|$", text, re.M) or "---" not in text:
        warns.append("缺少表格 (建议用表格说明触发方式 × 回应)")
    faq = [h for h in headings if re.match(r"^\d+\.", h)]
    if len(faq) < 6:
        warns.append(f"编号问答 {len(faq)} 条 (建议 >= 8)")
    if not re.search(r"文档索引|读者|README", text):
        warns.append("缺少文档索引表")
    return errors, warns


def walk_md(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in sorted(files):
            if f.endswith(".md"):
                yield os.path.join(base, f)


def main():
    ap = argparse.ArgumentParser(description="pattern-hub 模式合规检查器 (L1)")
    ap.add_argument("root", nargs="?", default=".", help="扫描目录 (默认当前目录)")
    ap.add_argument(
        "--pattern", default="all", choices=["all", "ai-readable-doc", "qa-doc"]
    )
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--exclude", default="", help="逗号分隔的目录名, 跳过不扫 (如 skills)"
    )
    args = ap.parse_args()

    excluded = [e.rstrip("/") for e in args.exclude.split(",") if e.strip()]
    rc, total = 0, 0
    for path in walk_md(args.root):
        rel = os.path.relpath(path, args.root)
        if any(rel == e or rel.startswith(e + "/") for e in excluded):
            continue
        rel = os.path.relpath(path, args.root)
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        total += 1
        errors, warns = check_ai_readable_doc(lines)
        if args.pattern in ("all", "qa-doc") and os.path.basename(path) in (
            "Q&A.md",
            "Q&A.md",
            "QA.md",
        ):
            errors, warns = check_qa_doc(lines, path)
        if errors:
            rc = 1
            print(f"FAIL {rel}: {'; '.join(errors)}")
        elif warns and not args.quiet:
            print(f"warn {rel}: {'; '.join(warns)}")
        elif not errors and not warns and not args.quiet:
            print(f"OK   {rel}")
    print(f"checked={total} rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
