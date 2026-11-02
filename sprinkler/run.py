#!/usr/bin/env python3

from sprinkler.arguments import parse_args
from sprinkler.plog.plog import plog_exec
from sprinkler.auto.auto import auto_exec
from sprinkler.config.config import config_exec
from sprinkler.connect.connect import connect_exec
from sprinkler.db.cli import db_exec
from sprinkler.network.network import network_exec
from sprinkler.auto.bandwidth.hixl import hixl_exec
from sprinkler.utils.utils import _RUN_PATH


def main(args=None):
    print('Sprinkler Path is {}'.format(_RUN_PATH))
    args = parse_args(args)
    plog_exec()
    auto_exec()
    config_exec()
    connect_exec()
    db_exec()
    network_exec()
    hixl_exec()


if __name__ == "__main__":
    main()
