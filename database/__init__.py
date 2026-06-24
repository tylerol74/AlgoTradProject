"""Database package for AlgoTradProject."""

from database.connection import get_connection
from database.schema import initialize_database

__all__ = ["get_connection", "initialize_database"]
