# SPDX-License-Identifier: GPL-3.0-or-later
import itertools
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


PRIORITY_COMMAND = 0
PRIORITY_POLL = 10
PRIORITY_BACKGROUND = 20


class BackgroundCancelled(Exception):
    """Ein Hintergrundjob wurde zugunsten eines Benutzerkommandos abgebrochen."""


@dataclass(order=True)
class Job:
    priority: int
    sequence: int

    name: str = field(compare=False)
    callback: Callable[..., Any] = field(compare=False)

    args: tuple = field(
        default_factory=tuple,
        compare=False,
    )

    kwargs: dict = field(
        default_factory=dict,
        compare=False,
    )

    cancel_event: Optional[threading.Event] = field(
        default=None,
        compare=False,
    )

    done_event: threading.Event = field(
        default_factory=threading.Event,
        compare=False,
    )

    result: Any = field(
        default=None,
        compare=False,
    )

    error: Optional[BaseException] = field(
        default=None,
        compare=False,
    )

    def wait(self, timeout=None):
        if not self.done_event.wait(timeout):
            raise TimeoutError(
                f"Job '{self.name}' wurde nicht rechtzeitig beendet"
            )

        if self.error is not None:
            raise self.error

        return self.result


class UIJobQueue:
    """
    Genau ein Worker verarbeitet alle Android-/VW-UI-Aktionen.

    Dadurch können keine zwei Polls oder Kommandos gleichzeitig auf
    der Volkswagen-App navigieren.
    """

    def __init__(self, log=None):
        self.log = log or (lambda level, message: None)

        self._queue = queue.PriorityQueue()
        self._sequence = itertools.count()

        self._stop_event = threading.Event()

        # Zeigt auf den Cancel-Event des gerade laufenden
        # Hintergrundjobs.
        self._active_background_cancel = None

        self._lock = threading.Lock()

        self._worker = threading.Thread(
            target=self._run,
            name="vw-ui-worker",
            daemon=True,
        )

    def start(self):
        if not self._worker.is_alive():
            self._worker.start()

    def stop(self):
        self._stop_event.set()

    def submit(
        self,
        name,
        callback,
        *args,
        priority=PRIORITY_BACKGROUND,
        cancellable=False,
        **kwargs,
    ):
        cancel_event = (
            threading.Event()
            if cancellable
            else None
        )

        job = Job(
            priority=priority,
            sequence=next(self._sequence),
            name=name,
            callback=callback,
            args=args,
            kwargs=kwargs,
            cancel_event=cancel_event,
        )

        # Ein Benutzerkommando darf einen gerade laufenden
        # abbrechbaren Hintergrundjob zur Beendigung auffordern.
        if priority == PRIORITY_COMMAND:
            with self._lock:
                if self._active_background_cancel is not None:
                    self._active_background_cancel.set()

        self._queue.put(job)

        self.log(
            "DEBUG",
            f"Job eingereiht: {name} "
            f"(Priorität {priority})",
        )

        return job

    def _run(self):
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                self.log(
                    "DEBUG",
                    f"Job startet: {job.name}",
                )

                if job.cancel_event is not None:
                    with self._lock:
                        self._active_background_cancel = (
                            job.cancel_event
                        )

                    # Der Callback bekommt den Event explizit.
                    job.kwargs["cancel_event"] = (
                        job.cancel_event
                    )

                job.result = job.callback(
                    *job.args,
                    **job.kwargs,
                )

            except BaseException as exc:
                job.error = exc

            finally:
                if job.cancel_event is not None:
                    with self._lock:
                        if (
                            self._active_background_cancel
                            is job.cancel_event
                        ):
                            self._active_background_cancel = None

                job.done_event.set()
                self._queue.task_done()

                self.log(
                    "DEBUG",
                    f"Job beendet: {job.name}",
                )


def check_cancel(cancel_event):
    """
    An sicheren Stellen innerhalb eines Hintergrund-Polls aufrufen.

    Nach einem tatsächlich abgesendeten Fahrzeugkommando darf diese
    Funktion bewusst NICHT mehr verwendet werden.
    """
    if (
        cancel_event is not None
        and cancel_event.is_set()
    ):
        raise BackgroundCancelled(
            "Hintergrundjob durch Benutzerkommando abgebrochen"
        )
