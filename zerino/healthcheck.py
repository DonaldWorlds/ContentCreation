"""Startup health checks.

Called from the capture daemon and the scheduler daemon BEFORE either does
anything real. Fails loud and early so the user gets a one-line "install
ffmpeg" error instead of a cryptic FileNotFoundError 30 minutes into a
streaming session.
"""
from __future__ import annotations

import shutil
import subprocess

from zerino.config import ZERNIO_API_KEY, get_logger

log = get_logger("zerino.healthcheck")


class HealthcheckError(RuntimeError):
    """Raised when a required external dependency is missing or broken."""


def _binary_version(name: str) -> str | None:
    """Return the first line of `<name> -version`, or None if not on PATH."""
    if shutil.which(name) is None:
        return None
    try:
        out = subprocess.run(
            [name, "-version"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    first_line = (out.stdout or out.stderr or "").splitlines()
    return first_line[0] if first_line else name


def check_ffmpeg(required: bool = True) -> None:
    """Verify ffmpeg + ffprobe are installed."""
    missing: list[str] = []
    for name in ("ffmpeg", "ffprobe"):
        version = _binary_version(name)
        if version is None:
            missing.append(name)
        else:
            log.info("healthcheck: %s OK -> %s", name, version)

    if not missing:
        return

    msg = (
        f"missing required binary: {', '.join(missing)}. "
        "Install ffmpeg and make sure it's on your PATH "
        "(macOS: `brew install ffmpeg`, Windows: install + add to PATH)."
    )
    if required:
        raise HealthcheckError(msg)
    log.warning("healthcheck: %s", msg)


def check_zernio_api_key(required: bool = True) -> None:
    """Verify ZERNIO_API_KEY is set in the environment."""
    if ZERNIO_API_KEY:
        log.info("healthcheck: ZERNIO_API_KEY present (len=%d)", len(ZERNIO_API_KEY))
        return
    msg = (
        "ZERNIO_API_KEY is not set. Add it to .env at the project root: "
        "ZERNIO_API_KEY=<your-key-from-zernio-dashboard>"
    )
    if required:
        raise HealthcheckError(msg)
    log.warning("healthcheck: %s", msg)


def run_capture_healthcheck() -> None:
    """Checks the capture daemon needs to pass before starting."""
    check_ffmpeg(required=True)
    # API key NOT required for capture — capture only writes DB rows.
    # Scheduler is the one that talks to Zernio.


def run_scheduler_healthcheck() -> None:
    """Checks the scheduler daemon needs to pass before starting."""
    check_ffmpeg(required=True)   # scheduler renders before posting
    check_zernio_api_key(required=True)


if __name__ == "__main__":
    # `python -m zerino.healthcheck` — quick command-line health probe.
    import sys

    log.info("running full healthcheck...")
    failed = []
    for name, fn in (
        ("ffmpeg", lambda: check_ffmpeg(required=True)),
        ("zernio_api_key", lambda: check_zernio_api_key(required=True)),
    ):
        try:
            fn()
        except HealthcheckError as e:
            failed.append((name, str(e)))

    if failed:
        print("\nFAIL:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)

    print("\nOK: all healthchecks passed.")
