WSGI_SERVER = False
if WSGI_SERVER = True:
    FLEX_PRINT_STD_ERR = True
    FLEX_PRINT_STD_OUT = False
    MESSAGING          = False
else:
    FLEX_PRINT_STD_ERR = False
    FLEX_PRINT_STD_OUT = True
    MESSAGING          = True
