import logging

from apps.worker.jobs import worker_loop

def main():
    logging.basicConfig(level=logging.INFO)
    worker_loop()

if __name__ == "__main__":
    main()
