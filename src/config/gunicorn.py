import multiprocessing
import os

bind = "localhost:8001"
preload_app = True
timeout = 200
max_requests = 500
max_requests_jitter = 10
worker_class = "gthread"
default_workers = min(multiprocessing.cpu_count() * 2 + 1, 4)
workers = int(os.getenv("GUNICORN_WORKERS", default_workers))
threads = int(os.getenv("GUNICORN_THREADS", 4))

accesslog = "-"
errorlog = "-"
