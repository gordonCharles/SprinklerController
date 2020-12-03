#!/usr/bin/python
'''
This provides a class for controlling the Sequent MicroSystems set of 8-Relay stackable hats for the raspberry pi:
    https://sequentmicrosystems.com/index.php?route=product/product&product_id=50

The class obfuscates the address and pin assignments into a single ordered list of relays; however, that list will
always be dependent on the number of stacked cord and how their addresses are set (via jumpers).  The cards will be
ordered according the ordering of addresses provided during initialization.  I.e.

relays = relayCont([0x3f, 0x3c]) will provide control for 16 relays in a numerical list where relays 1 through 8
(list indices 0 through 7) will be on card 0 (address 0x3f) and relays 9 through 16 (indices 8 through 15) will
be on card 1.

This module is designed to be thread safe,  locking the bus resources  at the beginning of each method and releasing the
bus resources after the completion of the last bus transaction in the method.

Notes on checking for I2C addresses outside of python
======================================================
At the command prompt type one of these depending on whether you are using the I2C0 or I2C1 port:
sudo i2cdetect -y 0
//or
sudo i2cdetect -y 1

The 7 bit I2C address of all found devices will be shown (ignoring the R/W bit, so I2C address 0000 0110 is displayed as hex 03).
'''
import smbus
from threading import Lock
import time

#addressList   = [0x3f, 0x3c]

bus = smbus.SMBus(1)    # 0 = /dev/i2c-0 (port I2C0), 1 = /dev/i2c-1 (port I2C1)
busLock = Lock()

TI_PCA9534A_INPORT_REG_ADD	            = 0x00
TI_PCA9534A_OUTPORT_REG_ADD	            = 0x01
TI_PCA9534A_POLARITY_INVERSION_REG_ADD	= 0x02
TI_PCA9534A_CONFIG_REG_ADD		        = 0x03

addressMap = {'InPort'   : TI_PCA9534A_INPORT_REG_ADD,
              'OutPort'  : TI_PCA9534A_OUTPORT_REG_ADD,
              'Polarity' : TI_PCA9534A_POLARITY_INVERSION_REG_ADD,
              'Config'   : TI_PCA9534A_CONFIG_REG_ADD}

revAddressMap = {}
for key in addressMap:
    revAddressMap[addressMap[key]] = key

addressMapDescriptions = {'InPort'  : "The logical value seen on the TI PCA9534 P[7:0] pins, representing the cummulative effects of the OutPort, Polarity and Config registers",
                          'OutPort' : "Values to be exclusive OR'd with the Polarity register before being driven on the the TI PCA9534 P[7:0] pins (for pins enabled in the config register",
                          'Polarity': "Not used. When set to 1 Polarity register bits will invert the corresponding OutPort bits being driven on the the TI PCA9534 P[7:0] pins",
                          'Config'  : "Output enalbe bits for each of the TI PCA9534 P[7:0] pins"}

relayMaskList = [0x01, 0x04, 0x02, 0x08, 0x40, 0x10, 0x20, 0x80] # Maps 9534A registers bits to relays
regSize       = len(relayMaskList)  # needed to support multiple relay daughter cards

ALL_RELAYS_IN_NORMAL_STATE = 0x00
ALL_ENABLE_RELAY_CONTROL   = 0x00
ALL_DISABLE_RELAY_CONTROL  = 0xff
ALL_CONTROLS_NOT_INVERTED  = 0x00

RC_FAIL_TO_WRITE_OUTPORT_REG            = 1
RC_FAIL_TO_WRITE_POLARITY_INVERSION_REG = 2
RC_FAIL_TO_WRITE_CONFIG_REG             = 3
RC_FAIL_TO_READ_INPORT_REG              = 4
RC_FAIL_TO_READ_OUTPORT_REG             = 5
RC_FAIL_TO_READ_POLARITY_INVERSION_REG  = 6
RC_FAIL_TO_READ_CONFIG_REG              = 7
RC_INIT_FAILURE                         = 8

class relayError(Exception):
    """Exception Class for valorNPI"""
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return (repr(self.value))


class relayCont:
    """
    Class for controlling a Sequent MicroSystems set of 8-Relay stackable hats for the raspberry pi:
    https://sequentmicrosystems.com/index.php?route=product/product&product_id=50
    Sequent Microsystems supplies a source c-code driver with makefile and documentation for use on
    the command line as well as a python sprinkler controller dependent on making calls to the driver
    through the OS.  This solution should provide longevity independent of Sequent MicroSystems.

    This class is intended to be used via a "with" construct such as:

    with relayController.relayCont() as relays:
        relays.closeNOrelays(relayList)

    where relayList is a list of the indices to set to Normally Open (when the common (COM) pin on the relay is connected
    to the Normally Open (NO) pin through a mechanical switch.  When cleared the NO pin is not connected to anything and
    the COM pin is connected to the Normally Closed (NC) pin.

    Relays are simple devices and do not have a programmable interface.  The 8-Relay board integrates a TI PCA9534
    I2C I/O expander, a Toshiba TBD62083AFNG driver which can support the current drive required to control the
    SONGLE SRD-05VDC-SL_C relays.  The relays have a typical on current of 71mA - no max current is provided.  The
    TBD62083AFNG driver data sheet lacks a specification for a max current drive for all outputs simultaneously at
    100% duty cycle.  Based on the provided information the circuit should be able to support all 8 relays having
    NO connected at the same time; however, the information is not complete to guarantee this.  There is an ambient
    temperature dependence to stay under the the maximum die temperature, which can not be determined with the provided
    information.

    Attributes:
        returncode(int)      - see definitions for code interpretation
        verboseness          - varying degree of print statements


    Methods:
        open()                        - non-preferred method to initialize
        closeNOrelays(relayList)      - provided list of integers will have relays enabled, connecting NO to COM
        close()                       - non-preferred method to disable
        getNumCards()                 - returns the number of cards defined in this header - not discovered on board
        getAddressList()              - returns the address list - the list passed to __init__
        getAddressMap()               - returns the address map for the TI PCA9534
        getAddressMapDescriptions()   - returns the register descriptions for the TI PCA9534
        writeReg(card, regAdd, value) - write a <value> to register at address <regAdd> on card number <card>
        readReg(card, regAdd)         - returns a list of bytes (always 1 in length read from register at address
                                        <regAdd> on card number <card>
        verbose(level)                - Set verbosness:
                                            0 - silent
                                            1 - high level command reporting
                                            2 - register level command reporting
    """
    def __init__(self, addressList):
        self.returncode = 0
        self.verboseness = 0
        self.shadowCopy = [{} for _ in range(len(addressList))]
        self.addressList = addressList  # 7 bit address (will be left shifted to append the read write bit in
                                        # bus.write_byte_data, bus.write_i2c_block_data and bus.read_i2c_block_data

    def open(self):
        self.__enter__()

    def __enter__(self):
        if self.verboseness > 0:
           print("Initializing TI PCA9534(s) ...")
        for index, address in enumerate(self.addressList):
            self.writeReg(card=index, regAdd=addressMap['OutPort'],  value=ALL_RELAYS_IN_NORMAL_STATE) # disconnect all NO relay pins
            self.writeReg(card=index, regAdd=addressMap['Polarity'], value=ALL_CONTROLS_NOT_INVERTED)  # re-write default value (should be redundant with PoR value)
            self.writeReg(card=index, regAdd=addressMap['Config'],   value=ALL_ENABLE_RELAY_CONTROL)   # enable control of all relays through Outport
            # Checking configuration succeeded
            registerVal = self.readReg(card=index, regAdd=addressMap['InPort'])
            if registerVal[0] != 0x00:
                errorString = f'Failed to initialize card {index} at address {hex(address)}.  Expected 0xFF on READ INPORT and read {hex(registerVal[0])}'
                raise (relayError(errorString))
        if self.verboseness > 0:
            print("Initializing TI PCA9534(s) successful")
        return(self)

    def reinit(self):
        for index, address in enumerate(self.addressList):
            self.writeReg(card=index, regAdd=addressMap['Polarity'], value=ALL_CONTROLS_NOT_INVERTED)  # re-write default value (should be redundant with PoR value)
            self.writeReg(card=index, regAdd=addressMap['Config'], value=ALL_ENABLE_RELAY_CONTROL)  # enable control of all relays through Outport

    def closeNOrelays(self, relayList):
        if self.verboseness > 0:
            print("Start of Setting Relays")
        registerWriteVal = []
        for i in range(len(self.addressList)):
            registerWriteVal.append(0)
        for relay in relayList:
            relayCountFromZero = relay - 1
            registerIndex = int(relayCountFromZero/regSize)
            registerWriteVal[registerIndex] += relayMaskList[relayCountFromZero % regSize]
        for index, address in enumerate(self.addressList):
            if registerWriteVal[index] > -1:
                self.writeReg(card=index, regAdd=addressMap['OutPort'], value=registerWriteVal[index])
        time.sleep(0.25)
        # The following was added to handle HW corruption of the TI PCA9534 until the hardware is fixed
        corruption = False
        corruptionFixed = False
        for card, address in enumerate(self.addressList):
            for regAdd in self.shadowCopy[card]:
                registerVal = self.readReg(card=card, regAdd=regAdd)
                if registerVal[0] != self.shadowCopy[card][regAdd]:
                    corruption = True
                    print(f"Register {revAddressMap[regAdd]} on card at {hex(self.addressList[card])} is corrupt.  Read {hex(registerVal[0])}, expected {hex(self.shadowCopy[card][regAdd])}")
                    self.writeReg(card=card, regAdd=regAdd, value=self.shadowCopy[card][regAdd])
        if corruption:
            corruptionFixed = True
            for card, address in enumerate(self.addressList):
                for regAdd in self.shadowCopy[card]:
                    registerVal = self.readReg(card=card, regAdd=regAdd)
                    if registerVal[0] != self.shadowCopy[card][regAdd]:
                        corruptionFixed = False
                        print(f"Register {revAddressMap[regAdd]} on card at {hex(self.addressList[card])} is corrupt.  Read {hex(registerVal[0])}, expected {hex(self.shadowCopy[card][regAdd])}")
                        print("Corruption not corrected")
        else:
            print("No Corrupiton detected")
        if corruptionFixed:
            print("Corruption Corrected")
        # END of HW workaround
        if self.verboseness > 0:
            relayListString = ''
            for _ in relayList:
                relayListString += f" {_},"
            relayListString = relayListString[:-1]
            print(f"Closed relay(s): {relayListString}")
        return(0)

    def checkState(self):
        # This method was added to handle HW corruption of the TI PCA9534 until the hardware is fixed and is called when
        # everything should be off to ensure everything is turned off
        if self.verboseness > 0:
            print("Checking register values against shadow copies")
        corruption = False
        corruptionFixed = False
        for card, address in enumerate(self.addressList):
            for regAdd in self.shadowCopy[card]:
                registerVal = self.readReg(card=card, regAdd=regAdd)
                if registerVal[0] != self.shadowCopy[card][regAdd]:
                    corruption = True
                    print(f"Register {revAddressMap[regAdd]} on card at {hex(self.addressList[card])} is corrupt.  Read {hex(registerVal[0])}, expected {hex(self.shadowCopy[card][regAdd])}")
                    self.writeReg(card=card, regAdd=regAdd, value=self.shadowCopy[card][regAdd])
        if corruption:
            corruptionFixed = True
            for card, address in enumerate(self.addressList):
                for regAdd in self.shadowCopy[card]:
                    registerVal = self.readReg(card=card, regAdd=regAdd)
                    if registerVal[0] != self.shadowCopy[card][regAdd]:
                        corruptionFixed = False
                        print(f"Register {revAddressMap[regAdd]} on card at {hex(self.addressList[card])} is corrupt.  Read {hex(registerVal[0])}, expected {hex(self.shadowCopy[card][regAdd])}")
                        print("Corruption not corrected")
        else:
            print("No Corrupiton detected")
        if corruptionFixed:
            print("Corruption Corrected")
        # END of HW workaround

    def getNumCards(self):
        return (len(self.addressList))

    def getAddressList(self):
        return (self.addressList)

    def getAddressMap(self):
        return (addressMap)

    def getAddressMapDescriptions(self):
        return(addressMapDescriptions)

    def writeReg(self, card, regAdd, value):
        busLock.acquire()
        try:
            if self.verboseness > 1:
                print(f"Writing value: {hex(value)} to Card {card} @ {hex(self.addressList[card])}, {revAddressMap[regAdd]}, {hex(regAdd)}")
            if regAdd in self.shadowCopy[card]:
                registerVal = bus.read_i2c_block_data(self.addressList[card], regAdd, 1)
                if registerVal[0] != self.shadowCopy[card][regAdd]:
                    print(f"Register {revAddressMap[regAdd]} on card at {hex(self.addressList[card])} is corrupt.  Read {hex(registerVal[0])}, expected {hex(self.shadowCopy[card][regAdd])}")
            self.shadowCopy[card][regAdd] = value
            rc = bus.write_byte_data(self.addressList[card], regAdd, value)
        except:
            self.returncode = RC_FAIL_TO_WRITE_OUTPORT_REG
            busLock.release()
            raise (relayError(f'Could not write to {revAddressMap[regAdd]} register on card at {hex(self.addressList[card])}'))
        busLock.release()

    def readReg(self, card, regAdd):
        busLock.acquire()
        try:
            registerVal = bus.read_i2c_block_data(self.addressList[card], regAdd, 1)
        except:
            self.returncode = RC_FAIL_TO_WRITE_OUTPORT_REG
            busLock.release()
            raise (relayError(f'Could not read from {revAddressMap[regAdd]} register on card at {self.addressList[card]}'))
        if self.verboseness > 1:
            registerValHex = [hex(registerVal[_]) for _ in range(len(registerVal))]
            print(f"Read value: {registerValHex} from Card {card} @ {hex(self.addressList[card])}, {revAddressMap[regAdd]}, {hex(regAdd)}")
        busLock.release()
        return(registerVal)

    def close(self):
        self.__exit__(None, None, None)

    def __exit__(self, exception_type, exception_value, traceback):
        if self.verboseness > 0:
           print("Disabling control of all relays through TI PCA9534(s) ...")
        for index, address in enumerate(self.addressList):
            self.writeReg(card=index, regAdd=addressMap['Config'],   value=ALL_DISABLE_RELAY_CONTROL)   # disable control of all relays through Outport
            # Checking configuration succeeded
            registerVal = self.readReg(card=index, regAdd=addressMap['InPort'])
            if registerVal[0] != 0x00:
                raise (relayError(f'Failed to disable card {index} at address {hex(address)}.  Expected 0x00 on READ INPORT and read {hex(registerVal[0])}'))
        if self.verboseness > 0:
            print("Success, relays disabled.")
        return(self)

    def verbose(self, level):
        self.verboseness = level
