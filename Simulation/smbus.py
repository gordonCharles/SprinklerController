#!/usr/bin/python
'''
This provides a debug smbus module for running on systems without a I2C controller.  This device acts like
a 256 byte memory, storing writes to each 8-bit address provided in the byte immediately following the
command byte.
'''
from FlexPrint import fprint
from config import *

MEMORY_SIZE = 256
NUM_DEVICES = 128
RC_I2C_FAIL_TO_WRITE   = 1
RC_I2C_FAIL_TO_READ    = 2


class SMBusError(Exception):
    """Exception Class for SMBus"""
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return (repr(self.value))

class SMBus:

    def __init__(self, i2cPort):
        self.returncode = 0
        self.verboseness = 1
        self.raiseErrorsEn = False
        self.i2cPort = i2cPort  # i2cPort can be 0 or 1 (nominally 1 on raspberry pi
        self.memory = [[0x00 for _ in range(MEMORY_SIZE)] for address in range(NUM_DEVICES)]

    def write_byte_data(self, i2c_address, reg_address, reg_value):
        if self.verboseness > 0:
            fprint(f"Writing value: {hex(reg_value)} register address {hex(reg_address)} @ i2c address {hex(i2c_address)}")
        if self.raiseErrorsEn:
            self.returncode = RC_I2C_FAIL_TO_WRITE
            raise (SMBusError(f'Could not write to {hex(reg_address)} register @ i2c address {hex(i2c_address)}'))
        self.memory[i2c_address][reg_address] = reg_value
        return(0)

    def read_i2c_block_data(self, i2c_address, reg_address, length):
        if self.raiseErrorsEn:
            self.returncode = RC_I2C_FAIL_TO_READ
            raise (SMBusError(f'Could not read from {hex(reg_address)} register @ i2c address {hex(i2c_address)}'))
        readValue = self.memory[i2c_address][reg_address:reg_address+length]
        readValueString = ''
        for _ in range(len(readValue)):
            readValueString += readValueString + hex(readValue[_])
        if self.verboseness > -1:
            fprint(f"Read value: {readValueString} register address {hex(reg_address)} @ i2c address {hex(i2c_address)}")
        return readValue

    def verbose(self, level):
        self.verboseness = level

    def raiseErrors(self, raiseErrorsEn):
        self.raiseErrorsEn = raiseErrorsEn
