"""Web automation subsystem — DOM-based browser record & replay.

Absorbed natively from the external webversionpw module (no external
dependency): a record -> store -> replay pipeline built on
undetected_chromedriver + injected in-page JavaScript.  Headless-first,
DOM-only: locator chains replace screen coordinates, explicit waits replace
fixed sleeps, and nothing depends on vision/OCR.

Public surface:
- record_web_session()  capture a browser session into a web session JSON
- replay_session()      execute a web session on a per-chain shared browser
- ReplayConfig          replay tuning (speed / native actions)
"""
from .engine import ReplayConfig, replay_session, replay_config_from_item
from .recorder import record_web_session

__all__ = [
    "ReplayConfig",
    "replay_session",
    "replay_config_from_item",
    "record_web_session",
]
