from fastapi import FastAPI

from app.agent_runtime.agent_os import create_app

app = create_app()


def get_app() -> FastAPI:
    """ASGI entrypoint helper for uvicorn/gunicorn."""
    return app
