"""Initialize the unified zerino.db schema.

Runs both the capture-side (recordings, markers, clips, exports, streamers)
and publishing-side (scheduled_jobs, job_events) schemas against the single
DB at zerino.config.DB_PATH.
"""

from zerino.config import DB_PATH, get_logger
from zerino.db.init_db import create_database
from zerino.publishing.init_db import init_db as init_publishing_db


def migrate() -> None:
    log = get_logger("zerino.db.migrate")
    log.info("Initializing unified DB at %s", DB_PATH)
    create_database()
    init_publishing_db()
    log.info("Migration complete.")


if __name__ == "__main__":
    migrate()
