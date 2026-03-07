from __future__ import annotations

import asyncio
import logging

from app.db import Base, engine, ensure_sqlite_schema, session_scope
from app.services.notification_service import NotificationService


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema()
    with session_scope() as db:
        service = NotificationService(db)
        result = await service.warm_long_term_cache()
        service.log_job("nas_prewarm_cache", "success", result)
        logger.info(result)


if __name__ == "__main__":
    asyncio.run(main())
