import os
import sys
import subprocess
import datetime

from sprinkler.arguments import get_args
from sprinkler.utils.utils import _RUN_PATH, find_all_shell


def config_exec():
    args = get_args()
    if args.superpod_conf:
        from sprinkler.config.superpod_info import superpod_conf
        superpod_conf()
    if args.sp_info:
        from sprinkler.config.superpod_info import get_superpod_info
        get_superpod_info(args.sp, args.ca)
    if args.modify_pwd:
        from sprinkler.config.superpod_info import modify_passwd
        modify_passwd(args.sp, args.ca, args.xlsx)
