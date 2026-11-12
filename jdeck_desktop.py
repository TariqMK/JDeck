"""JDeck Desktop: a desktop app for JDownloader 2. Sends links and shows downloads through
JDownloader's local API on your network (no cloud). Run `python jdeck_desktop.py`."""

import sys

from desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
