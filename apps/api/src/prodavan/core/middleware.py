"""Middleware registration helpers (P0 core register pattern)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def register_cors(app: FastAPI, *, allow_origins: list[str]) -> None:
    """Register CORS in one place (main.py must not scatter add_middleware)."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
