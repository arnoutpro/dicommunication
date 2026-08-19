"""`python -m app` starts the desktop launcher."""

from multiprocessing import freeze_support

from app.launcher import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
