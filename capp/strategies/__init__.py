"""
capp.strategies — strategy layer over capp.signals for CTraderApp.

Migrated from cBot CStrategies / mq4 SanStrategies for the active execution path.
"""

from .strategies import CStrategies, ProfitTrail, trail_exit

__all__ = ["CStrategies", "ProfitTrail", "trail_exit"]
