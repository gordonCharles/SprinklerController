
from config import *
import sys

def fprint(*args, **kwargs):
    if FLEX_PRINT_STD_ERR:
        print(*args, file=sys.stderr, **kwargs)
    if FLEX_PRINT_STD_OUT:
        print(*args, **kwargs)