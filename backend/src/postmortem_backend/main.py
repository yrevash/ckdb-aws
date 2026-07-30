"""Application entrypoint."""

from __future__ import annotations

from .api import create_app
from .config import Settings


app = create_app()


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "postmortem_backend.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
