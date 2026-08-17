"""MetaTrader 5 integration layer.

The rest of the application never imports ``MetaTrader5`` directly.  It talks to
:class:`~app.mt5.gateway.Mt5Gateway`, which has two implementations:

* :class:`~app.mt5.real.RealMt5Gateway` - the live terminal via the official
  ``MetaTrader5`` package (Windows only).
* :class:`~app.mt5.mock.MockMt5Gateway` - a deterministic in-process broker used
  for development, automated tests and demos.

All calls are funnelled through :class:`~app.mt5.manager.Mt5Runtime`, which
serialises them onto a single worker thread because the MetaTrader5 package is a
process-wide singleton bound to one terminal and one logged-in account.
"""

from .credentials import Mt5Credentials
from .gateway import Mt5Gateway
from .manager import Mt5Client, Mt5Runtime, get_runtime

__all__ = [
    "Mt5Client",
    "Mt5Credentials",
    "Mt5Gateway",
    "Mt5Runtime",
    "get_runtime",
]
