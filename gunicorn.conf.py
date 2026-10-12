"""One process owns all demo sessions; four threads handle independent requests."""
import os

bind = f"0.0.0.0:{int(os.environ.get('PORT', '10000'))}"
workers = 1
worker_class = 'gthread'
threads = 4
timeout = 60
graceful_timeout = 30
keepalive = 5
preload_app = False
reload = False
accesslog = '-'
errorlog = '-'
loglevel = 'info'
# No query strings, headers, request bodies or configuration values in access logs.
access_log_format = '%(h)s %(m)s %(U)s %(s)s'


def on_starting(server):
    # Refuse accidental overrides through dashboard CLI/env settings.
    if server.cfg.workers != 1 or server.cfg.worker_class_str != 'gthread' or server.cfg.threads != 4:
        raise RuntimeError('Demo requires exactly one gthread worker with four threads')
    if server.cfg.reload or server.cfg.preload_app:
        raise RuntimeError('Demo requires reload and preload to be disabled')
