#!/usr/bin/env python3
"""Trading app entry: csys connectivity + capp business loop."""

from csys import load_config
from capp import CTraderApp

if __name__ == "__main__":
    config = load_config()
    app = CTraderApp(config,active_strategy=5)
    app.run()
