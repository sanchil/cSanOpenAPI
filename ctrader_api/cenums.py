from enum import Enum

class SIG(Enum):
    """Trading Signals - equivalent to C# SIG enum"""
    
    HOLD = 101
    BUY = 102
    SELL = 103
    CLOSE = 104
    TRADE = 105
    NOTRADE = 106
    SIDEWAYS = 107
    NOSIG = 108

    # Optional: Add helper methods
    def is_long(self) -> bool:
        return self == SIG.BUY

    def is_short(self) -> bool:
        return self == SIG.SELL

    def is_neutral(self) -> bool:
        return self in (SIG.HOLD, SIG.NOSIG, SIG.NOTRADE, SIG.SIDEWAYS)

    def __str__(self):
        return self.name