"""
app.api.deps
------------
Common API dependencies shared across endpoints (database session, auth).

Import these in routers rather than reaching into ``app.core`` directly, so the
dependency surface stays in one place:

    from app.api.deps import get_db, get_current_user
"""

from app.core.database import get_db
from app.core.security import get_current_user

__all__ = ["get_db", "get_current_user"]
