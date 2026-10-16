import uvicorn

from .app import create_app, under_prefix
from .config import load

cfg = load()
app = create_app(cfg)
uvicorn.run(under_prefix(app, cfg.base_path) if cfg.base_path else app, host=cfg.host, port=cfg.port, log_level="info")
