"""Entry point for the bundled app (py2app runs a script, not `-m`)."""
import sys

from mac_app.app import main

sys.exit(main())
