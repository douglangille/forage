import uvicorn

from .app import create_app
from .config import load

cfg = load()
uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info")
