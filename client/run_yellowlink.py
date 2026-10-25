"""PyInstaller entry point for YellowLink (see yellowlink.spec)."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from yellowlink.__main__ import main

    main()
