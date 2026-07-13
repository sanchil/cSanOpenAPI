"""Backward-compat shim — enums now live in :mod:`ctrader_api.ctypes`. """

from .ctypes import DECAY_STRATEGY, SIG

__all__ = ["SIG", "DECAY_STRATEGY"]
