"""Pure domain layer.

Nothing in this package may import FastAPI, SQLAlchemy or MetaTrader5.  All
trading mathematics lives here so it can be unit-tested deterministically
without a broker connection.
"""
