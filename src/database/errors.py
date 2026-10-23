"""Database errors that mean "retry later", not "this record is bad"."""

from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from ..shared_state import StoreUnavailable

TRANSIENT_DB_ERRORS = (
    StoreUnavailable,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    DatabaseTimeout,
)
