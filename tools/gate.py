#!/usr/bin/env python3
"""gate.py — 提交门禁 runner (通用质量门禁, L1 校验)

由 gate.yaml 声明检查器: 文档合规 / 目录结构 / 格式化 / 测试 / 覆盖率...
  pre-commit 只跑 stage=fast 且命中暂存文件的检查器 (秒级)
  CI 跑 stage=all (全量)

gate.yaml 格式:
  gates:
    <名称>:
      cmd: "<shell 命令>"          # 任意命令检查器 (退出码 0=通过)
      type: structure              # 或内置目录结构检查器
      require: [目录]              #   (structure) 必须存在的目录
      forbid:  [目录]              #   (structure) 禁止存在的路径
      on: ["glob 模式"]            # 暂存文件命中任一模式才运行 (pre-commit 场景)
      stage: fast | slow           # fast=提交也跑; slow=仅 CI (默认 fast)

用法:
  python3 tools/gate.py [--stage fast|slow|all] [--changed] [--config gate.yaml] [--quiet]
退出码: 0=全部通过, 1=存在失败
"""

import argparse
import fnmatch
import os
import re
import subprocess
import sys

DEFAULT_CONFIG = "gate.yaml"


def parse_gates(text):
    """极简 YAML 子集解析 (零依赖): gates -> 名称 -> {key: value|list}。
    支持: 顶层 (0) / gate 名 (2) / 字段 (4, 空值视为列表占位) / 列表项 (6)。"""
    gates, cur, list_key = {}, None, None
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent == 0:
            cur, list_key = None, None
        elif indent == 2:
            m = re.match(r"^([\w-]+):\s*(.*)$", s)
            if m:
                cur = m.group(1)
                gates.setdefault(cur, {})
                list_key = None
        elif indent == 4 and cur:
            m = re.match(r"^([\w-]+):\s*(.*)$", s)
            if m:
                key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
                if val:
                    gates[cur][key] = val
                    list_key = None
                else:
                    gates[cur][key] = []  # 空值 -> 列表占位
                    list_key = key
            else:
                list_key = None
        elif indent == 6 and cur and list_key:
            m = re.match(r"^-\s+(.+)$", s)
            if m:
                gates[cur][list_key].append(m.group(1).strip('"').strip("'"))
        else:
            list_key = None
    return gates


def load_config(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return parse_gates(fh.read())


def staged_files(root):
    r = subprocess.run(
        ["git", "-C", root, "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
    )
    return [f for f in r.stdout.splitlines() if f]


def check_structure(cfg, root):
    errs = []
    for d in cfg.get("require", []) or []:
        if not os.path.isdir(os.path.join(root, d)):
            errs.append(f"missing dir: {d}")
    for d in cfg.get("forbid", []) or []:
        if os.path.exists(os.path.join(root, d)):
            errs.append(f"forbidden path exists: {d}")
    return errs


def run_gate(name, cfg, root, stage, changed):
    if cfg.get("stage") == "slow" and stage != "all":
        return "SKIP(ci-only)", True
    if changed is not None:
        pats = cfg.get("on") or ["**"]
        if not any(fnmatch.fnmatch(f, p) for p in pats for f in changed):
            return "SKIP(no-match)", True
    if cfg.get("type") == "structure":
        errs = check_structure(cfg, root)
        if errs:
            print(f"FAIL {name}: {'; '.join(errs)}")
            return "FAIL", False
        return "PASS", True
    cmd = cfg.get("cmd")
    if not cmd:
        print(f"FAIL {name}: missing cmd/type")
        return "FAIL", False
    r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        out = (r.stdout + r.stderr).strip()
        print(f"FAIL {name}: {cmd}")
        if out:
            for ln in out.splitlines()[:15]:
                print(f"    {ln}")
        return "FAIL", False
    return "PASS", True


def main():
    ap = argparse.ArgumentParser(description="提交门禁 runner (L1)")
    ap.add_argument("--stage", default="all", choices=["fast", "slow", "all"])
    ap.add_argument(
        "--changed",
        action="store_true",
        help="只跑命中暂存文件的检查器 (pre-commit 场景)",
    )
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = os.getcwd()
    gates = load_config(os.path.join(root, args.config))
    if gates is None:
        print(f"no config: {args.config} (skip)")
        return 0
    changed = staged_files(root) if args.changed else None

    n_pass = n_skip = n_fail = 0
    for name, cfg in gates.items():
        if args.quiet and cfg.get("stage") == "slow" and args.stage != "all":
            continue
        status, ok = run_gate(name, cfg, root, args.stage, changed)
        if not args.quiet:
            print(f"{status:<14} {name}")
        if status == "PASS":
            n_pass += 1
        elif status == "SKIP(no-match)":
            n_skip += 1
        elif status == "SKIP(ci-only)":
            n_skip += 1
        else:
            n_fail += 1
    if not args.quiet:
        print(f"gate: pass={n_pass} skip={n_skip} fail={n_fail} (stage={args.stage})")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
