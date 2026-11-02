"""数据库 CLI：导入 / 统计 / 列表 / 机柜刀片查询。

命令入口由 run.py 调用 db_exec()，参数见 arguments.py 的 add_db。
"""

import sys
from pathlib import Path

from sprinkler.db import dao
from sprinkler.db.database import init_db
from sprinkler.db.importer import import_superpod, format_import_result


def _default_xlsx(sp):
    return Path(__file__).resolve().parents[1] / "data" / f"superpod_{sp}.xlsx"


def db_exec():
    """run.py dispatcher 回调：根据 arguments 执行数据库操作。"""
    args = get_args_ns()
    if not (args.db_import or args.db_stats or args.db_list or args.db_cab):
        return

    conn = init_db()
    try:
        if args.db_import:
            sp = (args.sp or "").strip().lower()
            xlsx = Path(args.db_xlsx).expanduser() if args.db_xlsx else _default_xlsx(sp)
            if not xlsx.exists():
                print(f"Excel 文件不存在: {xlsx}")
                sys.exit(1)
            if sp and not _is_sp(sp):
                print(f"无效超节点编号: {sp}，请输入 0-9/a-f 单字符")
                sys.exit(1)
            try:
                res = import_superpod(xlsx, sp=sp, overwrite=args.db_overwrite,
                                      cluster_name=args.db_name)
                print(format_import_result(res))
            except dao.ClusterExistsError as e:
                print(e)
                sys.exit(1)

        if args.db_stats:
            stat = dao.stats(conn)
            print(f"cluster={stat['cluster']} cabinet={stat['cabinet']} blade={stat['blade']}")
            for row in stat["blade_by_type"]:
                print(f"  blade[{row['device_type']}] = {row['n']}")

        if args.db_list:
            rows, total = dao.list_clusters(conn, keyword=args.db_keyword)
            print(f"集群总数: {total}")
            for r in rows:
                print(f"  id={r['id']} name={r['name']!r} 机柜数={r['cabinet_count']} "
                      f"created={r['created_at']} updated={r['updated_at']}")

        if args.db_cab:
            sp = (args.sp or "").strip().lower()
            cluster = dao.get_cluster_by_name(conn, args.db_name or f"superpod_{sp}")
            if not cluster:
                print(f"集群不存在: {args.db_name or f'superpod_{sp}'}")
                sys.exit(1)
            cabs = dao.list_cabinets(conn, cluster["id"])
            cab = next((c for c in cabs if c["name"] == args.db_cab), None)
            if not cab:
                print(f"机柜不存在: {args.db_cab}")
                sys.exit(1)
            blades = dao.list_blades(conn, cab["id"])
            print(f"机柜 {cab['name']} (ca={cab['ca']}, sp={cab['superpod_id']}) 刀片列表({len(blades)}):")
            for b in blades:
                print(f"  slot={b['slot']:<5} {b['device_type']:<4} board={b['board_type']:<24} "
                      f"bmc_ip={b['bmc_ip']:<18} ubm_ip={b['ubm_ip']:<18} "
                      f"os_ip={b['os_ip']}")
    finally:
        conn.close()


def _is_sp(sp):
    return len(sp) == 1 and sp in "0123456789abcdef"


def get_args_ns():
    from sprinkler.arguments import get_args
    return get_args()
