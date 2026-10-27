"""A fresh-sample barrier, not a sleep or retry of the workload."""
import time


def observe_quiescence(samples, condition, *, after, limit, pid, alsa,
                       timeout=5.0, required=3, clock=time.monotonic):
    """Require consecutive actual samples after caller-proven owned cleanup.

    The caller owns the condition and notifies on each appended sample/fault.
    Above-limit samples reset the streak but remain in the immutable history.
    No new workload is run and no descriptor is forcibly closed here.
    """
    deadline = clock() + timeout
    cursor = 0
    streak = []
    observed = []
    with condition:
        while True:
            assert clock() < deadline, {'failure': 'quiescent FD recovery deadline', 'limit': limit, 'observed': observed}
            for item in samples[cursor:]:
                cursor += 1
                # The measurement must have STARTED after cleanup. Receipt time
                # alone could misclassify a delayed pre-cleanup health response.
                if item['request_started_monotonic'] <= after:
                    continue
                observed.append(item)
                assert item['pid'] == pid and item['alsa_fds'] == alsa, item
                assert item['midi'] == 'CONNECTED' and item['health'] == 'ok', item
                assert item['application'] == 'READY', item
                assert item['fds'] < 768, 'actual FD headroom abort'
                streak = streak + [item] if item['fds'] <= limit else []
                if len(streak) >= required:
                    assert clock() < deadline, 'quiescent FD recovery deadline'
                    return {'status': 'PASS', 'cleanup_barrier_monotonic': after,
                            'limit': limit, 'required': required,
                            'deadline_seconds': timeout, 'observed': observed,
                            'qualifying_samples': streak[-required:]}
            remaining = deadline - clock()
            if remaining <= 0:
                raise AssertionError({'failure': 'quiescent FD recovery deadline',
                                      'limit': limit, 'observed': observed})
            condition.wait(remaining)
