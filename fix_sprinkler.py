#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sprinkler 本地修复脚本 v2（在 D:\\sprinkler 上直接运行）

覆盖范围（刻意避开你已改过的 network/read_dev/write_dev 代码逻辑）：
  [Python]
  1. auto.py   —— 删 22 个无调用点函数；改写 5 个活函数；修 4 处脚本路径
  2. plog.py   —— 修 1 处必崩 import + 10 处 shell 路径
  3. arguments.py —— 下线 6 个「实现已不存在」的参数
  [Shell]
  4. 新建 sprinkler/utils/common.sh（补齐缺失的 log 函数 + 转发 config_util）
  5. 9 处 common.sh 引用、19 处 config_util.sh 引用、npuip.sh 引用 → 改为自包含绝对路径
  6. autoroot.sh / no_password.sh（全仓不存在）→ 注释并标注替代方案
  7. auto.sh 的 ping6_check.sh 层级修正
  [打包]
  8. setup.py python_requires 3.6 → 3.10；新建 MANIFEST.in 让 .sh/.txt/.csv/.conf 进包

用法（Windows PowerShell）：
    cd D:\\sprinkler
    python fix_sprinkler.py --dry-run       # 先看会改什么
    python fix_sprinkler.py                 # 实际修改（自动备份）
    python fix_sprinkler.py --skip-shell    # 只改 Python，不动 shell

幂等：已改过的位置会被识别并跳过，重复执行不会二次破坏。
"""

import argparse
import ast
import re
import shutil
import sys
import time
from pathlib import Path

# ------------------------------------------------------------------ 无调用点函数（直接删）
DEAD_FUNCS = [
    # 1213 / 1260 / general 检查批次（能力已迁到 auto/runtime/check）
    "bios_perf", "check_1213_all", "check_ping_1213", "check_1213_cc_config",
    "check_1213_df_config", "check_1213_card_register", "check_1213_port_status",
    "check_1213_credit_status", "check_1213_slot_status", "check_1213_vbus_status",
    "check_1260_port_status", "check_1260_port_use", "get_down_ip",
    "execute_sfu_command", "check_credit", "packet_capture",
    # 实现已不存在、且参数同时下线的 6 个
    "setrc_local", "npu_lock", "jetty_test", "p2p_sendrecv", "test_snr", "npu_root",
]

# 参数名 → 对应调用表达式（用于先删 auto_exec 分支）
BRANCHES = {
    "set_rc": "setrc_local()",
    "npu_lock": "npu_lock()",
    "jetty": "jetty_test(args)",
    "p2p_sendrecv": "p2p_sendrecv(args)",
    "test_snr": "test_snr(args)",
    "npu_root": "npu_root(args)",
}

# CLI flag（用于删 arguments.py 的注册行）
ARG_FLAGS = ["set-rc", "npu-lock", "jetty", "p2p-sendrecv", "test-snr", "npu-root",
             "jetty-model", "udma-id", "ssh-ip"]

# ------------------------------------------------------------------ 整函数替换
REPLACEMENTS = {
    "enable_env": '''def enable_env(args):
    """环境使能检查 - 复用 -env-check 的 Python 实现（高性能模式 + 分bin）"""
    return env_check(args)
''',
    "single_operator": '''def single_operator(args):
    """单算子测试 - 原 single_operator.sh 已不存在，功能待补"""
    print("ERROR: -single-operator 尚未提供 Python 实现（原 sprinkler/env/auto/single_operator.sh 已不存在）")
    print("       建议复用 oneclick/pta_common.py 的 TEST_SCRIPT_MAP 机制补写")
    return 1
''',
    "get_power": '''def get_power(args):
    """额定功率检查 - Python 实现，需配合 -test-rate-file 指定工作目录"""
    from sprinkler.auto.base.check_power import check_local as power_check_local

    test_rate_file = getattr(args, "test_rate_file", None)
    if not test_rate_file:
        print("ERROR: -get-power 需配合 -test-rate-file 指定 test/lib/host 所在目录")
        return 1
    result = power_check_local(test_rate_file, device_count=getattr(args, "device_count", 8))
    print(f"{'PASS' if result.status else 'FAIL'} | {result.name}: {result.message}")
    return 0 if result.status else 1
''',
    "get_route": '''def get_route(args):
    """EID 路由配置生成 - Python 实现（读 /home/source_dest_ips.txt 写 /lib/route.conf）"""
    from sprinkler.utils.get_route_eid import main as generate_route_conf
    return generate_route_conf()
''',
    "query_path": '''def query_path(args):
    """框内 d-u-d 链路查询 - 当前缺少实现，明确报错而不是抛裸 traceback"""
    src_device_name, src_unit_id, src_port_id, dest_ip = args.query_path
    try:
        from sprinkler.auto.runtime.general.query_path import query_path_execute
    except ImportError:
        print("ERROR: -query-path 功能尚未实现（缺少 sprinkler/auto/runtime/general/query_path.py）")
        print(f"       收到参数: device={src_device_name} unit={src_unit_id} "
              f"port={src_port_id} dest_ip={dest_ip}")
        return 1
    return query_path_execute(src_device_name, src_unit_id, src_port_id, dest_ip)
''',
}

# ------------------------------------------------------------------ 仅改路径行
PATH_FIX = {
    "stop_urma": (
        r"os\.path\.dirname\(_RUN_PATH\)\s*\+\s*'/sprinkler/env/auto/stop_npu_urma\.sh'",
        'str(package_path() / "network" / "stop_npu_urma.sh")',
    ),
    "check_eth": (
        r"os\.path\.dirname\(_RUN_PATH\)\s*\+\s*'/sprinkler/env/auto/check_eth\.sh'",
        'str(package_path() / "auto" / "health" / "check_eth.sh")',
    ),
    "update_code": (
        r"os\.path\.dirname\(_RUN_PATH\)\s*\+\s*'/sprinkler/env/auto/update_code\.sh'",
        'str(package_path() / "utils" / "update_code.sh")',
    ),
    "run_from_args": (
        r"os\.path\.dirname\(_RUN_PATH\)\s*\+\s*'/sprinkler/env/auto/auto\.sh'",
        'str(package_path() / "auto" / "auto.sh")',
    ),
}

# ------------------------------------------------------------------ 新建 common.sh
COMMON_SH_CONTENT = r'''#!/usr/bin/env bash
# 公共日志库：补齐历史上缺失的 sprinkler/auto/common.sh
# 用法: log LEVEL CHECK_ID "message"
# 本文件同时转发 config_util.sh，保证只 source 本文件的脚本仍可用 config_require_keys。

_COMMON_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${_COMMON_SH_DIR}/config_util.sh" ]; then
    # shellcheck source=/dev/null
    . "${_COMMON_SH_DIR}/config_util.sh"
fi

_common_now() { date '+%Y-%m-%d %H:%M:%S'; }

_COMMON_COLOR_OFF=$'\033[0m'
_COMMON_COLOR_OK=$'\033[32m'
_COMMON_COLOR_WARN=$'\033[33m'
_COMMON_COLOR_FAIL=$'\033[31m'
_COMMON_COLOR_INFO=$'\033[36m'
_COMMON_COLOR_DEBUG=$'\033[34m'

log() {
    local level="${1:-INFO}"
    local check_id="${2:-LOG}"
    shift || true
    shift || true
    local msg="$*"

    local color="$_COMMON_COLOR_OFF"
    case "$level" in
        OK)         color="$_COMMON_COLOR_OK" ;;
        WARN)       color="$_COMMON_COLOR_WARN" ;;
        FAIL|ERROR) color="$_COMMON_COLOR_FAIL" ;;
        INFO)       color="$_COMMON_COLOR_INFO" ;;
        DEBUG)      color="$_COMMON_COLOR_DEBUG" ;;
    esac

    printf "%s | %b%-5s%b | %s | %s\n" "$(_common_now)" "$color" "$level" "$_COMMON_COLOR_OFF" "$check_id" "$msg"
}
'''

MANIFEST_CONTENT = """include README.md
recursive-include sprinkler *.sh
recursive-include sprinkler *.txt
recursive-include sprinkler *.csv
recursive-include sprinkler *.conf
recursive-include sprinkler *.yaml
"""


# ================================================================== IO 工具
def read_text_keep_eol(path: Path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    has_bom = raw.startswith("\ufeff")
    if has_bom:
        raw = raw[1:]
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw, eol, has_bom


def write_text_keep_eol(path: Path, text: str, eol: str, has_bom: bool):
    data = ("\ufeff" if has_bom else "") + text
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(data)


def func_spans(src: str):
    tree = ast.parse(src)
    spans = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            spans[node.name] = (start, node.end_lineno)
    return spans


# ================================================================== auto.py
def fix_auto_py(src: str, log):
    original = len(src.split("\n"))

    # ---- 1) 先删 auto_exec 里的 6 个分支（否则函数仍有"调用"）
    for arg_name, call in BRANCHES.items():
        pattern = rf"\n[ \t]*if args\.{arg_name}:\n[ \t]*{re.escape(call)}\n"
        if re.search(pattern, src):
            src = re.sub(pattern, "\n", src, count=1)
            log.append(f"    [删分支] if args.{arg_name}")
        else:
            log.append(f"    [跳过] if args.{arg_name} 分支不存在")

    # ---- 2) 整函数替换
    for name, new_body in REPLACEMENTS.items():
        spans = func_spans(src)
        if name not in spans:
            log.append(f"    [跳过] {name}: 未找到定义")
            continue
        start, end = spans[name]
        body = "\n".join(src.split("\n")[start - 1:end])
        still_old = (
            "'/sprinkler/env/" in body
            or "os.path.dirname(_RUN_PATH)" in body
            or "from sprinkler.env" in body
            or "import sprinkler.env" in body
        )
        if not still_old:
            log.append(f"    [跳过] {name}: 已是新实现")
            continue
        lines = src.split("\n")
        lines[start - 1:end] = new_body.rstrip("\n").split("\n")
        src = "\n".join(lines)
        log.append(f"    [改写] {name}")

    # ---- 3) 删 22 个死函数
    for name in DEAD_FUNCS:
        spans = func_spans(src)
        if name not in spans:
            log.append(f"    [跳过] {name}: 不存在")
            continue
        refs = len(re.findall(rf"\b{name}\s*\(", src))
        if refs > 1:
            log.append(f"    [跳过] {name}: 仍有 {refs - 1} 处调用")
            continue
        start, end = spans[name]
        lines = src.split("\n")
        cut_end = end
        if cut_end < len(lines) and lines[cut_end].strip() == "":
            cut_end += 1
        del lines[start - 1:cut_end]
        src = "\n".join(lines)
        log.append(f"    [删除] {name}")

    # ---- 4) 路径行
    for name, (pattern, repl) in PATH_FIX.items():
        if re.search(pattern, src):
            src = re.sub(pattern, repl, src)
            log.append(f"    [改路径] {name}")
        else:
            log.append(f"    [跳过] {name}: 旧路径未匹配")

    # ---- 5) 清理孤立注释 + 空行
    for junk in ("\n# 1213相关检查\n", "\n# 1260相关\n", "\n# cpu1213/npu1213/sfu1260通用检查\n"):
        if junk in src:
            src = src.replace(junk, "\n")
            log.append(f"    [清理] 孤立注释 {junk.strip()}")
    before = len(src.split("\n"))
    src = re.sub(r"\n(?:[ \t]{1,}\n)+", "\n\n", src)
    src = re.sub(r"\n{3,}", "\n\n", src)
    if len(src.split("\n")) != before:
        log.append("    [整理] 压缩多余空行")

    # ---- 6) package_path 导入
    if re.search(r"^from sprinkler\.utils\.utils import .*\bpackage_path\b", src, re.M):
        log.append("    [跳过] package_path 已存在")
    else:
        old_import = "from sprinkler.utils.utils import _RUN_PATH, find_all_shell"
        if old_import in src:
            src = src.replace(
                old_import,
                "from sprinkler.utils.utils import _RUN_PATH, find_all_shell, package_path",
                1,
            )
            log.append("    [导入] 追加 package_path")
        else:
            log.append("    [警告] 未找到 utils 导入行，请手动补 package_path")

    log.append(f"    [规模] {original} 行 -> {len(src.split(chr(10)))} 行")
    return src


# ================================================================== plog.py
def fix_plog_py(src: str, log):
    old = "from sprinkler.env.plog.plog_analysis import main_fault_matcher"
    new = "from sprinkler.plog.plog_analysis import main_fault_matcher"
    if old in src:
        src = src.replace(old, new)
        log.append("    [修复] plog_analysis import 前缀")
    else:
        log.append("    [跳过] plog_analysis import 已正确")

    pattern = r"os\.path\.dirname\(_RUN_PATH\)\s*\+\s*'/sprinkler/env/plog/([^']+)'"
    src, n = re.subn(pattern, r'str(package_path() / "plog" / "\1")', src)
    if n:
        log.append(f"    [改路径] {n} 处 env/plog/*.sh")
    else:
        log.append("    [跳过] 未匹配到 env/plog 旧前缀")

    if re.search(r"^from sprinkler\.utils\.utils import .*\bpackage_path\b", src, re.M):
        log.append("    [跳过] package_path 已存在")
    else:
        old_import = "from sprinkler.utils.utils import _RUN_PATH, find_all_shell"
        if old_import in src:
            src = src.replace(
                old_import,
                "from sprinkler.utils.utils import _RUN_PATH, find_all_shell, package_path",
                1,
            )
            log.append("    [导入] 追加 package_path")
        else:
            log.append("    [警告] 未找到 utils 导入行")
    return src


# ================================================================== arguments.py
def _paren_delta(line: str) -> int:
    """统计一行里未被字符串包裹的括号净值"""
    delta, quote, esc = 0, None, False
    for ch in line:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "(":
            delta += 1
        elif ch == ")":
            delta -= 1
    return delta


def fix_arguments_py(src: str, log):
    for flag in ARG_FLAGS:
        lines = src.split("\n")
        hit = False
        i = 0
        while i < len(lines):
            line = lines[i]
            if "add_argument" in line and f'"-{flag}"' in line:
                depth, j = 0, i
                while j < len(lines):
                    depth += _paren_delta(lines[j])
                    if depth <= 0:
                        break
                    j += 1
                del lines[i:j + 1]
                hit = True
                break
            i += 1
        if hit:
            src = "\n".join(lines)
            log.append(f"    [下线参数] -{flag}")
        else:
            log.append(f"    [跳过] -{flag} 已不在参数表")
    return src


# ================================================================== setup.py
def fix_setup_py(src: str, log):
    if "python_requires='>=3.6'" in src:
        src = src.replace("python_requires='>=3.6'", "python_requires='>=3.10'")
        log.append("    [修改] python_requires >=3.6 -> >=3.10")
    elif "python_requires='>=3.10'" in src:
        log.append("    [跳过] python_requires 已是 3.10")
    else:
        log.append("    [警告] 未找到 python_requires 行")

    for ver in ("3.7", "3.8", "3.9"):
        line = f"        'Programming Language :: Python :: {ver}',\n"
        if line in src:
            src = src.replace(line, "")
            log.append(f"    [清理] classifier {ver}")
    return src


# ================================================================== shell
def rel_to_repo(depth: int) -> str:
    """从脚本所在目录回到仓库根的相对前缀"""
    return "../" * (depth + 1)


def abs_expr(rel: str) -> str:
    return '$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/' + rel


OLD_DIRDIR = '$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")'


def fix_one_shell(path: Path, repo_root: Path, log, backup_dir):
    src, eol, has_bom = read_text_keep_eol(path)
    original = src
    rel = path.relative_to(repo_root / "sprinkler")
    depth = len(rel.parts) - 1
    prefix = rel_to_repo(depth)

    utils_cfg = prefix + "sprinkler/utils/config_util.sh"
    utils_cmn = prefix + "sprinkler/utils/common.sh"
    utils_ip = prefix + "sprinkler/utils/npuip.sh"

    lines = src.split(eol)
    out = []
    changed = []

    for line in lines:
        stripped = line.strip()

        # (a) config_util.sh 引用
        if re.match(r'^(\.|source)\s+"?\$\{?PARENT_DIR\}?/config_util\.sh"?$', stripped):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f'{indent}. "{abs_expr(utils_cfg)}"')
            changed.append("config_util")
            continue

        # (b) common.sh 引用（$SCRIPT_DIR 形式）
        if re.match(r'^(\.|source)\s+"\$\{?SCRIPT_DIR\}?/common\.sh"', stripped) or \
           re.match(r'^(\.|source)\s+"?\$\{?SCRIPT_DIR\}?/common\.sh"?$', stripped):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f'{indent}. "{abs_expr(utils_cmn)}"')
            changed.append("common")
            continue

        # (c) common.sh 引用（COMMON_SH= 赋值形式）
        if re.match(r'^\s*COMMON_SH="\$SCRIPT_DIR/common\.sh"', line):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f'{indent}COMMON_SH="{abs_expr(utils_cmn)}"')
            changed.append("common")
            continue

        # (d) common.sh 引用（dirname×2 + /auto/common.sh）
        if "common.sh" in line and OLD_DIRDIR in line and ("source" in line or "." in line):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f'{indent}. "{abs_expr(utils_cmn)}"')
            changed.append("common")
            continue

        # (e) npuip.sh
        if OLD_DIRDIR + "/npuip.sh" in line:
            out.append(line.replace(OLD_DIRDIR + "/npuip.sh", abs_expr(utils_ip)))
            changed.append("npuip")
            continue

        # (f) autoroot.sh / no_password.sh —— 全仓不存在，注释并标注
        if ("autoroot.sh" in line or "no_password.sh" in line) and not line.lstrip().startswith("#"):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f'{indent}# [DEPRECATED] 引用文件已不存在，功能由 sprinkler -no-pass 取代')
            out.append(f'{indent}# {line.lstrip()}')
            out.append(f'{indent}:  # no-op（原命令已下线）')
            changed.append("missing")
            continue

        out.append(line)

    src = eol.join(out)

    # (g) auto.sh 的 ping6 层级
    if path.name == "auto.sh":
        if '"ping6_check.sh"' in src:
            src = src.replace('"ping6_check.sh"', '"health/ping6_check.sh"')
            changed.append("ping6")
        elif '"health/ping6_check.sh"' in src:
            pass

    if src != original:
        if backup_dir is not None:
            backup_dir.mkdir(exist_ok=True)
            dst = backup_dir / path.name
            if not dst.exists():
                shutil.copy2(path, dst)
        write_text_keep_eol(path, src, eol, has_bom)
        log.append(f"    [修改] {', '.join(sorted(set(changed)))}")
        return True
    return False


def fix_shells(repo_root: Path, log, backup_dir=None):
    sh_files = sorted((repo_root / "sprinkler").rglob("*.sh"))
    touched = 0
    for p in sh_files:
        sub = []
        if fix_one_shell(p, repo_root, sub, backup_dir):
            touched += 1
            log.append(f"  {p.relative_to(repo_root)}: {sub[0].replace('    [修改] ', '')}")
    log.append(f"  shell 共修改 {touched} 个文件")


# ================================================================== 主流程
def find_repo_root(start: Path) -> Path:
    for c in [start, start.parent, Path.cwd(), Path.cwd().parent]:
        if (c / "sprinkler" / "auto" / "auto.py").exists():
            return c
    raise SystemExit("未找到仓库根，请用 --root D:\\sprinkler 指定")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-shell", action="store_true", help="只改 Python，不动 shell")
    args = ap.parse_args()

    repo = find_repo_root(Path(args.root).resolve())
    print(f"仓库根: {repo}")
    print(f"模式  : {'DRY-RUN' if args.dry_run else '实际修改'}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = repo / f".fix_backup_{stamp}"

    # ---------- Python 文件
    py_targets = {
        repo / "sprinkler" / "auto" / "auto.py": fix_auto_py,
        repo / "sprinkler" / "plog" / "plog.py": fix_plog_py,
        repo / "sprinkler" / "arguments.py": fix_arguments_py,
        repo / "setup.py": fix_setup_py,
    }

    for path, fn in py_targets.items():
        if not path.exists():
            print(f"\n!! 缺少 {path}（跳过）")
            continue
        print(f"\n=== {path.relative_to(repo)} ===")
        src, eol, has_bom = read_text_keep_eol(path)
        log = []
        new_src = fn(src, log)
        print("\n".join(log))
        try:
            compile(new_src, str(path), "exec")
        except SyntaxError as e:
            print(f"!! 语法校验失败，放弃写入: {e}")
            continue
        if new_src == src:
            print("   （无实际变化）")
            continue
        if args.dry_run:
            print("   [dry-run] 未写入")
            continue
        backup.mkdir(exist_ok=True)
        shutil.copy2(path, backup / path.name)
        write_text_keep_eol(path, new_src, eol, has_bom)
        print(f"   已写入（备份 {backup.name}/{path.name}）")

    # ---------- 新建 common.sh / MANIFEST.in
    if not args.skip_shell:
        print("\n=== 新建文件 ===")
        common_sh = repo / "sprinkler" / "utils" / "common.sh"
        if common_sh.exists():
            print(f"   {common_sh.relative_to(repo)} 已存在，跳过")
        elif args.dry_run:
            print(f"   [dry-run] 将创建 {common_sh.relative_to(repo)}")
        else:
            common_sh.parent.mkdir(parents=True, exist_ok=True)
            common_sh.write_text(COMMON_SH_CONTENT, encoding="utf-8", newline="\n")
            print(f"   已创建 {common_sh.relative_to(repo)}")

    manifest = repo / "MANIFEST.in"
    if manifest.exists():
        print(f"   MANIFEST.in 已存在，跳过")
    elif args.dry_run:
        print("   [dry-run] 将创建 MANIFEST.in")
    else:
        manifest.write_text(MANIFEST_CONTENT, encoding="utf-8", newline="\n")
        print("   已创建 MANIFEST.in")

    # ---------- shell
    if args.skip_shell:
        print("\n--skip-shell：跳过 shell 修改")
    else:
        print("\n=== shell 公共库与路径 ===")
        if args.dry_run:
            print("   [dry-run] 逐文件预检：")
            tmp_log = []
            sh_files = sorted((repo / "sprinkler").rglob("*.sh"))
            for p in sh_files:
                s, _, _ = read_text_keep_eol(p)
                if ("PARENT_DIR}/config_util.sh" in s or "common.sh" in s
                        or OLD_DIRDIR + "/npuip.sh" in s or "autoroot.sh" in s
                        or "no_password.sh" in s):
                    tmp_log.append(str(p.relative_to(repo)))
            print("   命中文件:", len(tmp_log))
            for t in tmp_log[:40]:
                print("    -", t)
        else:
            if backup.exists() is False:
                backup.mkdir(exist_ok=True)
            log = []
            fix_shells(repo, log, backup)
            print("\n".join(log))

    print("\n完成。")
    if not args.dry_run and backup.exists():
        print(f"回滚：从 {backup} 取回原文件。")


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
