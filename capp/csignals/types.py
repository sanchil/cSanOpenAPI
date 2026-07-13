"""Backward-compat shim — DTYPE / T_SIG / SIG live in csys.ctypes. """

from csys.ctypes import DTYPE, SIG, T_SIG

__all__ = ["DTYPE", "T_SIG", "SIG"]
