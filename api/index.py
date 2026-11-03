"""Vercel entrypoint: exports the FastAPI `app`."""

from app.main import create_app

app = create_app()
