"""Entry point for Diaricat Live service."""

from __future__ import annotations

import logging
import sys

import uvicorn

from diaricat_live.settings import Settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    settings = Settings()

    # Allow overriding port via CLI arg
    if len(sys.argv) > 1:
        try:
            settings.port = int(sys.argv[1])
        except ValueError:
            pass

    from diaricat_live.api.app import create_app

    app = create_app(settings)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
