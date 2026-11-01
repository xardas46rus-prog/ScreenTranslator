from multiprocessing import freeze_support
from launcher.__main__ import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
