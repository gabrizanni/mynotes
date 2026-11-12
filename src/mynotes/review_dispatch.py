"""Bounded independent review work; transport cancellation follows the job."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextvars import ContextVar
from threading import Event

cancel_event = ContextVar('mynotes_review_cancel_event', default=None)


def dispatch(items, operation, resources, *, workers=2):
    """Return input-ordered results, never overlap potentially shared edits.

    Only running operations acquire durable attempts. A failure stops dispatch,
    cancels owned transports and retains already completed packet receipts.
    """
    if workers not in (1, 2):
        raise ValueError('Review dispatch permits one or two workers')
    items = list(items)
    if not items:
        return []
    parent = cancel_event.get()
    stopped = Event()
    results = [None] * len(items)
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='mynotes-review')
    pending = list(range(len(items)))
    running = {}

    def run(index):
        token = cancel_event.set(stopped)
        try:
            if stopped.is_set():
                raise RuntimeError('Review cancelled before dispatch')
            return operation(items[index])
        finally:
            cancel_event.reset(token)

    try:
        while pending or running:
            if parent is not None and parent.is_set():
                raise RuntimeError('Section review cancelled')
            occupied = set().union(*(keys for _, keys in running.values()))
            # Preserve ordering at conflicts; independent packets may overlap.
            blocked = set()
            for index in list(pending):
                keys = set(resources(items[index]))
                if len(running) >= workers:
                    break
                if keys & (occupied | blocked):
                    blocked.update(keys)
                    continue
                running[pool.submit(run, index)] = (index, keys)
                pending.remove(index)
                occupied.update(keys)
            done, _ = wait(running, timeout=.1, return_when=FIRST_COMPLETED)
            # Observe every completion/error before dispatching another packet.
            for future in done:
                index, _ = running.pop(future)
                results[index] = future.result()
        return results
    except BaseException:
        stopped.set()
        for future in running:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
