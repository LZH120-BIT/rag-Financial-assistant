import logging
import sys


class MyLogger(logging.Logger):
    def __init__(self, name: str):
        super().__init__(name)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        self.addHandler(handler)
        self.setLevel(logging.INFO)