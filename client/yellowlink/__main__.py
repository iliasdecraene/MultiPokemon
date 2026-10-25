import multiprocessing


def main():
    multiprocessing.freeze_support()
    from .app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
