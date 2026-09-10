"""Thread-safe cooperative cancellation shared by the GUI and task worker."""

from threading import Event, Lock
from uuid import uuid4


class TaskCancellationError(RuntimeError):
    """A step was stopped before entering its tool callable."""


class AgentTaskControl:
    def __init__(self):
        self.task_id = str(uuid4())
        self._cancelled = Event()
        self._boundary = Lock()

    def request_cancel(self):
        with self._boundary:
            self._cancelled.set()

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    def begin_step(self):
        """Linearize step admission against cancellation; an admitted step may finish."""
        with self._boundary:
            return not self._cancelled.is_set()
