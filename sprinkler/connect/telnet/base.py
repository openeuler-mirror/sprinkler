"""Telnet connection base connector using telnetlib3."""
import asyncio
import re
import socket
import time
import telnetlib3

from sprinkler.connect.base import BaseConnector
from sprinkler.connect.targets import ConnectionTarget


class TelnetConnector(BaseConnector):
    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        raise NotImplementedError("Subclasses must implement _run_commands")

    async def _establish_connection(self, target: ConnectionTarget):
        target = ConnectionTarget.from_value(target)
        target_error = target.validate()
        if target_error:
            raise ValueError(target_error)

        try:
            reader, writer = await asyncio.wait_for(
                telnetlib3.open_connection(target.host, target.port),
                timeout=self.connect_timeout,
            )
        except Exception as exc:
            raise RuntimeError(f"{self._classify_error(exc)}: {exc}") from exc

        return reader, writer

    async def _close_connection(self, writer):
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
