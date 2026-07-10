""" Backward-compat shim — DTYPE / T_SIG / SIG now live in ctrader_api.ctypes. """

from ctrader_api.ctypes import DTYPE, SIG, T_SIG

__all__ = ["DTYPE", "T_SIG", "SIG"]
