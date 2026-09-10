"""Bounded deadlines for synchronous Agent calls, without killing threads."""

import logging
from threading import BoundedSemaphore, Event, Lock, Thread

try:
    from .tool_registry import ToolExecutionError
except ImportError:
    from tool_registry import ToolExecutionError


_CALL_SLOTS = BoundedSemaphore(4)
LOGGER = logging.getLogger(__name__)


class AgentTimeoutError(ToolExecutionError):
    """The caller stopped waiting; an already running operation may still finish."""


def call_with_timeout(operation, timeout, label):
    if not _CALL_SLOTS.acquire(blocking=False):
        raise AgentTimeoutError("后台调用仍在收尾，请稍后再试。")
    done = Event()
    expired = Event()
    admission = Lock()
    outcome = {}

    def invoke():
        try:
            with admission:
                if expired.is_set():
                    return
            outcome["result"] = operation()
        except BaseException as error:
            outcome["error"] = error
        finally:
            _CALL_SLOTS.release()
            done.set()

    thread = Thread(target=invoke, name="agent-call", daemon=True)
    try:
        thread.start()
    except Exception:
        _CALL_SLOTS.release()
        raise
    if not done.wait(timeout):
        with admission:
            expired.set()
        LOGGER.debug("Tool timeout -> %s", label)
        raise AgentTimeoutError(f"{label} 超时，任务已停止；已开始的操作可能仍在收尾，请检查结果后再尝试。")
    if "error" in outcome:
        error = outcome["error"]
        if isinstance(error, Exception):
            raise error
        raise ToolExecutionError(f"{label} 未能正常结束。") from error
    return outcome.get("result")
