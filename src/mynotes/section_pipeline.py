"""One ordered reviewer behind a bounded writer; first-section gate."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from .review_dispatch import cancel_event


class SectionPipeline:
    def __init__(self, check):
        self.check = check
        self.stopped = Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mynotes-section')
        self.pending = []
        self.error = None

    def _check(self, item):
        token = cancel_event.set(self.stopped)
        try:
            if self.stopped.is_set():
                return None
            return self.check(item)
        except BaseException as error:
            self.error = error
            self.stopped.set()
            raise
        finally:
            cancel_event.reset(token)

    def submit(self, item, *, first=False, final=False):
        self.poll()
        self.pending.append(self.pool.submit(self._check, item))
        if first:
            self.pending.pop(0).result()
        elif not final and len(self.pending)>=2:
            self.pending.pop(0).result()
        self.poll()

    def poll(self):
        if self.error is not None:
            raise self.error
        while self.pending and self.pending[0].done():
            self.pending.pop(0).result()

    def drain(self):
        while self.pending:
            self.pending.pop(0).result()
        self.poll()

    def close(self, *, cancel=False):
        if cancel:
            self.stopped.set()
        self.pool.shutdown(wait=True, cancel_futures=cancel)
