"""Run blocking provider/parser work in a killable fresh process boundary."""
from __future__ import annotations

from collections.abc import Callable
import multiprocessing
from multiprocessing.connection import Connection
import os
import signal
import traceback
from typing import Any


def _invoke(
    sender: Connection,
    callback: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    try:
        os.setsid()
        sender.send((True, callback(*args, **kwargs)))
    except BaseException:  # noqa: BLE001 - the parent needs a bounded failure receipt
        sender.send((False, traceback.format_exc()))
    finally:
        sender.close()


class IsolatedWork:
    """One result-bearing child whose whole process group can be stopped."""

    def __init__(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        self._receiver = receiver
        self._process = context.Process(
            target=_invoke,
            args=(sender, callback, args, kwargs),
            daemon=False,
        )
        try:
            self._process.start()
        except BaseException:
            sender.close()
            receiver.close()
            raise
        sender.close()

    @property
    def done(self) -> bool:
        return self._receiver.poll() or not self._process.is_alive()

    def result(self) -> Any:
        self._process.join()
        try:
            succeeded, payload = self._receiver.recv()
        except EOFError as error:
            raise RuntimeError(
                f"isolated work exited without a result (exit code {self._process.exitcode})"
            ) from error
        finally:
            self._receiver.close()
        if not succeeded:
            raise RuntimeError(f"isolated work failed:\n{payload}")
        return payload

    def terminate(self) -> None:
        if self._process.is_alive():
            try:
                os.killpg(self._process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            self._process.terminate()
            self._process.join(timeout=0.5)
        if self._process.is_alive():
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._process.kill()
        self._process.join()
        self._receiver.close()
