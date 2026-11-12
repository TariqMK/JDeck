"""JDeck Server: JDownloader 2 from any device on your network. Runs on the JDownloader
computer and serves the JDeck page behind a password. Run `python jdeck_server.py`."""

import sys

from server.app import main

if __name__ == "__main__":
    sys.exit(main())
