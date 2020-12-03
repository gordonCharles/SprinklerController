#!/usr/bin/python
'''
This provides a sprinkler controller application running an a raspberry pi with a Sequent MicroSystems set of
8-Relay stackable hats, one relay per valve (watering zone).  Save the i2c address('s) of the hats the interface
to the hats is obfuscated in RelayController.py.  This application focuses the functionality of providing a html
based GUI interface suitable for mobile devices, via almost entirely from the bootstrap 4 cascading style sheets
(css) and javascript.  Flask is used as a means to automate the creation of the html, providing a responsive UI
and moving the user input from the server back into python.  The little non-voltage storage required for maintaining
state is done via pickle and storing a configuration file in the file system.  Effort has been made to only store
the file at a low frequency, not every time the user changes a parameter on the GUI.  The system can support up
to 64 zones (limited by HW) and an "unlimited" number of timers.  The system supports manual on / off controls with
an auto-shut off feature that prevents the sprinklers from being accidentally left on for an extended period of time.
The system also supports designating each zone as a lower water demand zone.  These multizones can be overlapped so
they operate simultaneously when on the same timer.  This allows to have a set of drip lines for flowers (which often
have watering requirements of 2 hours once a week) to be grouped together.  As an example four zones can be watered at
the same time in 2 hours versus taking 8 hours - this allows for more ideal watering times.  The system will also
automatically handle any collisions from mutliple timers ensuring the watering durations will be respected for all
zones even if they collide in time.

Notes on checking for I2C addresses outside of python
======================================================
At the command prompt type one of these depending on whether you are using the I2C0 or I2C1 port:
sudo i2cdetect -y 0
//or
sudo i2cdetect -y 1

The 7 bit I2C address of all found devices will be shown (ignoring the R/W bit, so I2C address 0000 0110 is displayed as hex 03).
'''
DEBUG = False

from flask import Flask, redirect, url_for, render_template, request, session

from datetime import timedelta
from threading import Thread
import pickle
import datetime
import sys
import os
import re
import time
import copy
import queue
import RelayController
import socket
import json
import smtplib
import ssl
import argparse
from email.message import EmailMessage
'''
Imports from private are constants that need to be created for a specific userID.  You may also wish
to change the constant name GORDONS_EMAIL (unless your name is Gordon ;).  For ovious reasons private.py
is not incuded in the repo.
'''
from private import SUDO_PASSWORD as SUDO_PASSWORD  # Sudo password needed to pet the watchdog
from private import SENDER_EMAIL as SENDER_EMAIL    # E-mail account to use for sending e-mail notifications
from private import PASSWORD as PASSWORD            # Password for email acount to send notificaitons
from private import GORDONS_EMAIL as GORDONS_EMAIL  # Recipient email address
''' Recipient's cell phone numbers e-mail address
NOTE: You can e-mail a text message to a cell phone at no cost, at least for the major carriers in the US.
Each carrier has it's own format for the e-mail address as shown in the following example:

Provider	Format		                            Example Number: 4081234567
======================================================================================
Sprint	    phonenumber@messaging.sprintpcs.com     4081234567@messaging.sprintpcs.com
Verizon	    phonenumber@vtext.com	            	4081234567@vtext.com
T-Mobile	phonenumber@tmomail.net	            	4081234567@tmomail.net
AT&T	    phonenumber@txt.att.net	            	4081234567@txt.att.net

The repository hosting this file will include an excel spreadsheet which generates the e-mail addresses 
from the 10-digit number.
'''
from private import GORDONS_CELL as GORDONS_CELL    # Recipient's cell phone numbers e-mail address

# Need to know what system we are running on.  The "demo" version that runs on ubuntu in the cloud 
# must have some behavioral differences, especially regarding watchdog and for conveniance not use 
# a ramdisk
osinfo = os.uname()
if osinfo[1] == 'raspberrypi':
    piHost = True

if piHost:
    RAM_DISK           = '/var/ramdisk/'
else:
    RAM_DISK           = '/tmp/'

# A report file is written to for every watering action taken and the summary is sent to the 
# specified e-mail at the end of the week.
REPORT_FILE_NAME       = RAM_DISK + 'report.txt'
REPORT_DAY_OF_THE_WEEK = 'Sunday'
REPORT_TIME_OF_DAY     = '6:00PM'

# Service mode is needed as the live images from the camera can not be displayed when running
# in service mode.
serviceMode = False
parser = argparse.ArgumentParser()
parser.add_argument('--serviceMode', help='enables any internal changes required when running as a service versus running in the debugger',
                    action='store_true')
args = parser.parse_args()
if args.serviceMode:
    serviceMode = True
'''
To ensure we can quickly turn off the watch dog mode should an issue be introduced, the 
watchdog petting, which enables the watchdog the first time it is performed, must be 
enabled by the presense of the ENABLE_WATCHDOG flag being set to 1.  Moving or deleting
the config file will prevent the watdog from being run the next time the script is called.
This is useful to be able to disable the watchdog and then take the execution out of 
service mode.
'''
WATCH_DOG_ENABLE       = False
CONFIG_FILE            = "sc_config.txt"
if os.path.isfile(CONFIG_FILE):
    workingConfigFile = open(RAM_DISK + 'working_config.txt', "w")
    with open(CONFIG_FILE) as configFile:
        for line in configFile:
            workingConfigFile.write(line)
            parameter = line.rstrip()
            if parameter == "ENABLE_WATCHDOG=1":
                WATCH_DOG_ENABLE = True
                print("Watch Dog Enabled")
    workingConfigFile.close()
else:
    print("Running without a config file")

#WATCH_DOG_ENABLE       = False
WATCH_DOG_PET_INTERVAL = 5 # Watch Dog Petting inteval (must be less than 15 seconds or system will reboot
keepAlive              = 0


SSL_PORT               = 465  # For SSL
GMAIL_SMTP_SERVER      = "smtp.gmail.com"

relaysStackAddressList = [0x3f, 0x3b]  # Configure with the addresses of each stack

app = Flask(__name__)
app.secret_key = b'\x8dc\x83|$\xb9l\x90\x03\xd2<\xbc\xac>\x89\x84'
app.permanent_session_lifetime = timedelta(minutes=5)

END_OF_TIME = 32000000000

#### zones.html variables ####
zoneTable = [{'name': 'Curbside Lawn',     'relay': 1, 'on': True,  'wateringTime': 60, 'timer': 1, 'multiZone': False, 'dogDetectOn': True,  'detectCount': 0, 'manualStartTime': END_OF_TIME},
             {'name': 'Cherry Tree Lawn',  'relay': 2, 'on': False, 'wateringTime': 60, 'timer': 1, 'multiZone': False, 'dogDetectOn': False, 'detectCount': 0, 'manualStartTime': END_OF_TIME},
             {'name': 'Cherry Tree Roses', 'relay': 3, 'on': False, 'wateringTime': 60, 'timer': 1, 'multiZone': True,  'dogDetectOn': False, 'detectCount': 0, 'manualStartTime': END_OF_TIME},
             {'name': 'Maple Tree Lawn',   'relay': 4, 'on': False, 'wateringTime': 60, 'timer': 1, 'multiZone': False, 'dogDetectOn': False, 'detectCount': 0, 'manualStartTime': END_OF_TIME},
             {'name': 'Maple Tree Roses',  'relay': 5, 'on': False, 'wateringTime': 60, 'timer': 1, 'multiZone': False, 'dogDetectOn': False, 'detectCount': 0, 'manualStartTime': END_OF_TIME},
             {'name': 'BKYRD Lawn Fence',  'relay': 6, 'on': False, 'wateringTime': 60, 'timer': 1, 'multiZone': False, 'dogDetectOn': False, 'detectCount': 0, 'manualStartTime': END_OF_TIME},
             {'name': 'BKYRD Lawn House',  'relay': 7, 'on': False, 'wateringTime': 60, 'timer': 1, 'multiZone': False, 'dogDetectOn': False, 'detectCount': 0, 'manualStartTime': END_OF_TIME},
             {'name': 'Fence Flowers',     'relay': 8, 'on': False, 'wateringTime': 60, 'timer': 1, 'multiZone': False, 'dogDetectOn': False, 'detectCount': 0, 'manualStartTime': END_OF_TIME},
             {'name': 'BKYRD Flowers',     'relay': 9, 'on': False, 'wateringTime': 60, 'timer': 1, 'multiZone': False, 'dogDetectOn': False, 'detectCount': 0, 'manualStartTime': END_OF_TIME}]

dogWarning = False
wateringTimes = [0, 3, 5, 10, 15, 20, 25, 30, 40, 50, 60, 90, 120]

#### timers.html variables ####
timerTable = [{'labeled': True,   'selected': False, 'startTime': '8:00PM', 'Type': 'INT', 'Interval': 1, 'lastTimeOn': 0,
               'Sunday': 'checked', 'Monday': '', 'Tuesday': '', 'Wednesday': '', 'Thursday': '', 'Friday': '', 'Saturday': ''},
              {'labeled': False,  'selected': False, 'startTime': '8:00PM', 'Type': 'INT', 'Interval': 1, 'lastTimeOn': 0,
               'Sunday': 'checked', 'Monday': '', 'Tuesday': '', 'Wednesday': '', 'Thursday': '', 'Friday': '', 'Saturday': ''},
              {'labeled': False,  'selected': False, 'startTime': '8:00PM', 'Type': 'INT', 'Interval': 1, 'lastTimeOn': 0,
               'Sunday': 'checked', 'Monday': '', 'Tuesday': '', 'Wednesday': '', 'Thursday': '', 'Friday': '', 'Saturday': ''},
              {'labeled': True,   'selected': False, 'startTime': '8:00PM', 'Type': 'DoW', 'Interval': 1, 'lastTimeOn': 0,
               'Sunday': 'checked', 'Monday': '', 'Tuesday': '', 'Wednesday': '', 'Thursday': '', 'Friday': '', 'Saturday': ''},
              {'labeled': False,  'selected': False, 'startTime': '8:00PM', 'Type': 'DoW', 'Interval': 1, 'lastTimeOn': 0,
               'Sunday': 'checked', 'Monday': '', 'Tuesday': '', 'Wednesday': '', 'Thursday': '', 'Friday': '', 'Saturday': ''}
              ]

timerTypes = ['INT', 'DoW']

daysOfWeek = [('Sunday', 'S'), ('Monday', 'M'), ('Tuesday', 'T'), ('Wednesday', 'W'), ('Thursday', 'T'), ('Friday', 'F'), ('Saturday', 'S')]

intervals = [1, 2, 3, 4, 5, 6, 7, 14]

#### settings.html variables ####
config        = {'allOff'        : False,
                 'dogMode'       : True,
                 'weatherAdjust' : False}

autoShutOff   = {'multiZone'  : 60,
                 'singleZone' : 15}
relayShadow   = []

updateNVM             = 0 # Time from the last epoch in seconds since the last change to NVM data
NVM_UPDATE_INTERVAL   = 10 #NVM strcuture update interval in seconds
NVM_FILENAME          = os.path.abspath((os.path.join(os.path.dirname(__file__), 'sprinklerNVM.pkl')))
TIMER_SAMPLE_INTERVAL = 45 # Set to less than one minute to ensure start times are not missed.
DOG_WARNING_DURATION  = 60 # Dog warning sprinkler on duration in seconds
MIN_WATERING_TIME     = 120 # Minimum watering time after dog detection times have been subtracted from scheduled watering time

'''
Fake Time allows for simulation at faster than real time. Times as fast as 1600x have been run successfully 
on a Pi4.  
'''
FAKE_TIME_EN    = False
FAKE_TIME_SCALE = 200  # Multiple to scale time by
fakeTime        = 0    # Fake time value to use as an arbitrary starint point
fakeTimeStart   = 0    # Set to 1 to use fakeTime as the initial starting time instead of current real time

def setRelays(mode):
    ''' 
    Sets the values for all of the relays.  The relays are grouped in registers so coceptually all of them
    are updated each time any change value.  setRelays, keeps a shadow copy of the registers so only registers
    that change are updated.  Unfortuantely the Sequent MicroSystems 8-Relay stackable hats do not include 
    sufficient protection from back EMF generated by the solenoids in the sprinklers when shut off.  The 
    result is likley damaging to the I2C controller on the board and may affect it's lifetime.  This also
    results in occational corruption of the relay control registers.  I will design a replacment board based 
    on triacs which, only switch when the current is zero, avoiing the back EMF problem later.  For now this 
    function makes three attempts to wire the relays and will send a message if it fails to complete it's
    objective.   

    Args:
        mode (string): string to indicate type of thread setting the relays.

    Globals:
        zoneTable (list of dictionaries): data structure for per zone settings.
        relays (relayCont): relayCont instance for all, multiple hats with 8 each, relays.
        config (dictionary): data structure for configuration settings.
        relayShadow (list of ): data structure for per zone settings.

    Returns:
        Nothing

    Modifies:
        relays, relayShadow
    '''
    global zoneTable
    global relays
    global config
    global relayShadow

    newRelayShadow = []
    relayList      = []
    if not config['allOff']:
        for zone in range(len(zoneTable)):
            if zoneTable[zone]['on']:
                relayList.append(zoneTable[zone]['relay'])
                newRelayShadow.append(zone)
        #print("Turning on Relays : ", relayList)
    currentDatetime = localDatetime()  # datetime.datetime.now()
    textDayOfWeek = currentDatetime.strftime("%A, %b %-d")
    currentTime = currentDatetime.time()
    textTime = currentTime.strftime("%-I:%M%p")
    for _ in range(3):
        try:
            relays.closeNOrelays(relayList)
            break
        except:
            print(f"Failed attempt {_+1} to set relays")
            time.sleep(0.25)
            repeat = True
        sendTextMessage(messageSubject="I2C Bus Failure", messageText=f"I2C Bus Failur at {textTime}", recipient=GORDONS_CELL)
    with open(REPORT_FILE_NAME, "a+") as reportFile:
        for zone in newRelayShadow:
            if zone not in relayShadow:
                reportFile.write(f"Zone {zoneTable[zone]['name']} {mode} turned on at {textTime}, {textDayOfWeek}\r\n")
        for zone in relayShadow:
            if zone not in newRelayShadow:
                reportFile.write(f"Zone {zoneTable[zone]['name']} {mode} turned off at {textTime}, {textDayOfWeek}\r\n")
    relayShadow = newRelayShadow
    #relays.reinit()

def checkRelays():
    ''' 
    Wrapper created around checkState() method to hanled I2C bus faults due to Sequent MicroSystems 
    8-Relay stackable hats not hanlding back EMF properly.  In addition to currupting register values
    the back EMF can corrupt an in progress I2C transaction.  I2c bus faults occur less frequently than
    register corruption.   

    Globals:
        relays (relayCont): relayCont instance for all, multiple hats with 8 each, relays.

    Returns:
        Nothing
    '''
    global relays

    currentDatetime = localDatetime()  # datetime.datetime.now()
    currentTime = currentDatetime.time()
    textTime = currentTime.strftime("%-I:%M%p")

    for _ in range(3):
        try:
            relays.checkState()
            break
        except:
            print(f"Failed attempt {_} to set relays")
            time.sleep(0.25)
            repeat = True
        sendTextMessage(messageSubject="I2C Bus Failure", messageText=f"I2C Bus Failur at {textTime}", recipient=GORDONS_CELL)


def sendEmail(subject, textFile, recipient):
    ''' 
    Sends an email with subject and body defined by textFile to recipient.

    Args:
        subject (string): message subjet.
        textFile (string): pointer to text file
        recipient (string): e-mail address of recipient

    Returns:
        Nothing
    '''
    # generic email headers
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient

    with open(textFile) as fp:
        msg.set_content(fp.read())
    text = msg.as_string()
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(GMAIL_SMTP_SERVER, SSL_PORT, context=context) as server:
        server.login(SENDER_EMAIL, PASSWORD)
        server.sendmail(SENDER_EMAIL, recipient, text)
    print("Email Sent")


def sendTextMessage(messageSubject, messageText, recipient):
    ''' 
    Sends text message (as an outbound e-mail with subject and body defined by messageText to 
    recipient.

    Args:
        messageSubject (string): message subjet.
        messageText (string): message content
        recipient (string): phone number formated as e-mail address - carrier specific format

    Returns:
        Nothing
    '''
    message = "From: %s\r\n" % SENDER_EMAIL \
              + "To: %s\r\n" % recipient \
              + "Subject: %s\r\n" % messageSubject \
              + "\r\n" \
              + messageText

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(GMAIL_SMTP_SERVER, SSL_PORT, context=context) as server:
        server.login(SENDER_EMAIL, PASSWORD)
        server.sendmail(SENDER_EMAIL, recipient, message)
    print("Message Sent")


def configureTimerLables():
    ''' 
    On the timers.html page header rows for the timer tables need to be present for the first
    row and prior to any row with a differnt timer type than the preceeding rwo.  This function
    scans through the configurations to set which rows require a header.
    '''
    timerTable[0]['labeled'] = True
    for row in range(1, len(timerTable)):
        if timerTable[row]['Type'] == timerTable[row-1]['Type']:
            timerTable[row]['labeled'] = False
        else:
            timerTable[row]['labeled'] = True

def parseTime(timeString, default):
    ''' 
    parseTime peforms some slick handling of user input with a great degree of flexibility
    to allow users to enter time in the fewest number of characters.  It handles the following:
    1) entry in 24 hour or AM / PM automatically detected
    2) AM PM detection on either A, AM, P, PM.
    3) Automatic completion of times when minutes are not provided

    Examples:
    7:00 AM can be entered as 7, 7:00, 7a, 7A, 7am, 7AM, 7:00a, 7:00A, 7:00am, 7:00AM ...
    7:30 AM can be entered as 730, 730a, 730A, 730am, 730AM, 7:30, 7:30a, 7:30A, 7:30am, 7:30AM ... 
    8:30 PM can be entered as 830p, 830pm, 830PM, 8:30p, 8:30pm, 8:30P, 8:30PM, 2030, 20:30

    Args:
        timestring (string): user input
        default (string): value to return (exising value) should the input not be valid

    Returns:
        Time formated as HH:MM{PM/AM}
    '''
    regexPM  = re.compile('[^pP]')
    regexNum = re.compile('[^0-9]')
    newPM    = regexPM.sub('', timeString)
    if newPM.upper() == 'P':
        newTimeString = 'PM'
    else:
        newTimeString = 'AM'
    newTime  = regexNum.sub('', timeString)
    if len(newTime) == 0:
        return default
    else:
        newTimeInt = int(newTime)
    if newTimeInt > 0 and newTimeInt < 13:
        return f"{newTimeInt}:00{newTimeString}"
    elif newTimeInt > 12 and newTimeInt < 23:
        return f"{newTimeInt-12}:00PM"
    elif newTimeInt > 99 and newTimeInt < 1260 and newTimeInt%100 < 60:
        return f"{int(newTimeInt/100)}:{newTimeInt%100:02d}{newTimeString}"
    elif newTimeInt > 1299 and newTimeInt < 2360 and newTimeInt%100 < 60:
        return f"{int((newTimeInt-1200)/100)}:{newTimeInt%100}PM"
    else:
        return default

# Defining the rout page
@app.route("/")  # this sets the route to this page
def home():
	return redirect(url_for("zones"))

@app.route("/zones", methods=["POST", "GET"])
def zones():
    global updateNVM

    if request.method == "POST":
        zoneForm = request.form
        for key in zoneForm:
            if key == 'saveButton':
                doNothing = True  # Save simly triggers storage of selection boxes which don't trigger on change
            elif key == 'zoneButton':
                keypressed = zoneForm[key].split(' ')  #keypressed[0] = index, keypressed[1] = 'on', 'multizone' or 'dogDetectOn', keypressed[2] = state - 'on or 'off'
                index = int(keypressed[0])
                keyBolean = (keypressed[2] == 'on')
                zoneTable[index][keypressed[1]] = keyBolean
                if zoneTable[index][keypressed[1]] != 'on': # Manual control has been used to turn on / off a zone
                    updateNVM = time.time()
                    if keypressed[2] == 'on': # User has manually turned on zone
                        zoneTable[index]['manualStartTime'] = localTime()
            else:
                multiSelectKey = key.split(' ')
                index = int(multiSelectKey[0])
                zoneTable[index][multiSelectKey[1]] = int(zoneForm[key])
        setRelays("manually")
        return render_template("zones.html", zoneTable=zoneTable, wateringTimes=wateringTimes, timerTable=timerTable, content="true")
    else:
        return render_template("zones.html", zoneTable=zoneTable, wateringTimes=wateringTimes, timerTable=timerTable, content="true")


@app.route("/timers", methods=["POST", "GET"])
def timers():
    global updateNVM

    if request.method == "POST":
        timerForm = request.form
        updateNVM = time.time()
        print("timers upatedNVM : ", updateNVM)
        for day, abb in daysOfWeek: # checkboxes only return values when checked - so need to reset all checks to off
            for timer in range(len(timerTable)):
                timerTable[timer][day] = ''
        for key in timerForm:
            if key == 'timerButton':
                keypressed = timerForm[key].split(' ')
                if keypressed[0] == 'save':
                    doNothing = True
                elif keypressed[0] == 'add':
                    timerTable.append(timerTable[len(timerTable)-1])
                elif keypressed[0] == 'delete':
                    timerTable.pop(int(keypressed[1]))
                else:
                    for timer in range(len(timerTable)): # clear all selected timers - only one can be selected at a time (used for deleting timers
                        if int(keypressed[1]) == timer:
                            timerTable[timer]['selected'] = not timerTable[timer]['selected']
                        else:
                            timerTable[timer]['selected'] = False
            else:
                multiSelectKey = key.split(' ')
                index = int(multiSelectKey[0])
                if index < len(timerTable):
                    if multiSelectKey[1] == 'startTime':
                        timerTable[index]['startTime'] = parseTime(timerForm[key], default=timerTable[index]['startTime'])
                    elif multiSelectKey[1] == 'Interval':
                        timerTable[index]['Interval'] = int(timerForm[key])
                    else:
                        if timerForm[key] == 'on':  #Necessary as key state is retruned as on instead of checked???
                            storedValue = 'checked'
                        else:
                            storedValue = timerForm[key]
                        timerTable[index][multiSelectKey[1]] = storedValue
        configureTimerLables()
        return render_template("timers.html", timerTable=timerTable, timerTypes=timerTypes, daysOfWeek=daysOfWeek, intervals=intervals, content="true")
    else:
        return render_template("timers.html", timerTable=timerTable, timerTypes=timerTypes, daysOfWeek=daysOfWeek, intervals=intervals, content="true")

@app.route("/settings", methods=["POST", "GET"])
def settings():
    global updateNVM

    if request.method == "POST":
        settingForm = request.form
        updateNVM = time.time()
        print(settingForm, file=sys.stdout)
        for key in settingForm:
            if key == 'settingButton':
                if settingForm[key] != 'save':
                    config[settingForm[key]] = not config[settingForm[key]]
                    if settingForm[key] == 'allOff':
                        setRelays("manually")
            else: # must be auto-shutoff value
                autoShutOff[key] = int(settingForm[key])
        return render_template("settings.html", config=config,
                               wateringTimes=wateringTimes, autoShutOff=autoShutOff, content="true")
    else:
        return render_template("settings.html", config=config,
                               wateringTimes=wateringTimes, autoShutOff=autoShutOff, content="true")

configureTimerLables()

@app.route("/admin")
def admin():
	return redirect(url_for("user", name="Admin"))  # Now we when we go to /admin we will redirect to user with the argument "Admin!"

def saveState():
    ''' 
    The saveState thread loops, sleeping for NVM_UPDATE_INTERVAL, and storing the current user set configurations
    for the sprinkler system if they have been recently modified.  This provides a reasonable but not
    truely bulletproof soluttion for maintaining settings.  In the rare case where changes have been 
    made and power is lost after the changes but before the system has completed writing the changes
    there are two possible negative outcomes.  If the updates have not started the changes will be lost;
    however, the previous settings will be loaded once the system restarts.  If the updates are in 
    progress corruption of the configuration is possible; however, the filesystem is designed to survive
    partially written files as the underlying NAND flash is real failure rates and requires a fault 
    tolerant file system.  The proper behavior of this process has not been properly tested; however,
    the liklihood of an issue is very low and the system includes a watchdog and weekly reporting. 

    Globals:
        updateNVM (time): time of last update to NVM values
        zoneTable (list of dictionaries): data structure for per zone settings.
        timerTable (list of dictionaries): data structure for per timer settings.
        config (dictionary): data structure for configuration settings.
        autoShutOff (dictionary): Auto shut-off settings.

    Returns:
        Nothing

    Modifies:
        updateNVM
    '''
    global updateNVM
    global zoneTable
    global timerTable
    global config
    global autoShutOff

    while True:
        time.sleep(NVM_UPDATE_INTERVAL)
        timeDelta = time.time() - updateNVM
        if timeDelta > NVM_UPDATE_INTERVAL and timeDelta < 2.5 * NVM_UPDATE_INTERVAL:
            NVMzoneTable = copy.deepcopy(zoneTable)
            for zone in range(len(zoneTable)):
                NVMzoneTable[zone]['on'] = False # Turn off all sprinklers (virtually) before saving data structure
            with open(NVM_FILENAME, 'wb') as NVMfile:
                pickle.dump(NVMzoneTable, NVMfile)
                pickle.dump(timerTable,   NVMfile)
                pickle.dump(config,       NVMfile)
                pickle.dump(autoShutOff,  NVMfile)
            updateNVM = 0

def loadState():
    ''' 
    loads user set configurations for the sprinkler system. 

    Globals:
        NVM_FILENAME (string): filename constant for configuration settings.
        zoneTable (list of dictionaries): data structure for per zone settings.
        timerTable (list of dictionaries): data structure for per timer settings.
        config (dictionary): data structure for configuration settings.
        autoShutOff (dictionary): Auto shut-off settings.

    Returns:
        Nothing

    Modifies:
        zoneTable, timerTable, config, autoShutOff
   '''
    global NVM_FILENAME
    global zoneTable
    global timerTable
    global config
    global autoShutOff

    try: # Open NVM file if it exists otherwise use defaults
        with open(NVM_FILENAME, 'rb') as NVMfile:
            zoneTable   = pickle.load(NVMfile)
            timerTable  = pickle.load(NVMfile)
            config      = pickle.load(NVMfile)
            autoShutOff = pickle.load(NVMfile)
        for zone in range(len(zoneTable)):
            if zoneTable[zone]['wateringTime'] not in wateringTimes:
                zoneTable[zone]['wateringTime'] = min(wateringTimes, key=lambda wateringTime : abs(wateringTime - zoneTable[zone]['wateringTime']))
            zoneTable[zone]['manualStartTime'] = 0
        for timer in range(len(timerTable)):
            if timerTable[timer]['Type'] not in timerTypes:
                timerTable[timer]['Type'] = timerTypes[0]
            if timerTable[timer]['Interval'] not in intervals:
                timerTable[timer]['Interval'] = min(wateringTimes, key=lambda wateringTime : abs(wateringTime - timerTable[timer]['Interval']))

    except:
        print("config file not found, using defaults")


def localDatetime():
    ''' 
    datetime wrapper function which provides hooks to simulate at faster than real time 

    Globals:
        fakeTime (string): filename constant for configuration settings.
        fakeTimeStart (int): indicator of time or load time when zero

    Returns:
        datetime formated current datetime (real or simulated)
   '''
    global fakeTime
    global fakeTimeStart

    if FAKE_TIME_EN:
        if fakeTimeStart == 0:
            return datetime.datetime.now()
        else:
            return datetime.datetime.fromtimestamp(fakeTime)
    else:
        return datetime.datetime.now()

def localTime():
    ''' 
    time wrapper function which provides hooks to simulate at faster than real time 

    Globals:
        fakeTime (string): filename constant for configuration settings.
        fakeTimeStart (int): indicator of time or load time when zero

    Returns:
        time formated current time (real or simulated)
   '''
    global fakeTime
    global fakeTimeStart

    if FAKE_TIME_EN:
        if fakeTimeStart == 0:
            fakeTimeStart = time.time()
            fakeTime = fakeTimeStart
        else:
            fakeTime = FAKE_TIME_SCALE * (time.time() - fakeTimeStart) + fakeTimeStart
        return fakeTime
    else:
        return time.time()

def timerThread():
    ''' 
    The timerThread loops, sleeping for TIMER_SAMPLE_INTERVAL, every cycle it determines if any timers have
    triggered, creates a list of zones which should be turned as a result.  The list is then appended to the 
    queue of zones to be watered.  The queue is created with the understanding that "Multi" zones can be watered
    at the same time.  This thread also handles the manual overide.  Manaul overrides that start watering on 
    a zone result in an automatic shut off timer being created acording to the duration defined in the user 
    settings depending if the zone is "Multi" or not.  Manual overides to shut off in progress watering delay
    the watering until the override is removed, at which point watering removes.  Lastly the timer will send 
    an e-mal with the weekly report should the date / time match the configuration.  This process includes 
    a keepalive counter the watchdog monitors to ensure this thread is functioning.

    Globals:
        zoneTable (list of dictionaries): data structure for per zone settings.
        timerTable (list of dictionaries): data structure for per timer settings.
        config (dictionary): data structure for configuration settings.
        autoShutOff (dictionary): Auto shut-off settings.
        keepAlive (int): Keep alive counter for watchdog

    Returns:
        Nothing

    Modifies:
        zoneTable, relays
    '''
    global zoneTable
    global timerTable
    global config
    global autoShutOff
    global keepAlive

    pendingZones = queue.Queue()
    activeZones  = []
    wateringIdle = True
    endTimer     = 0
    currentZone  = 0
    oldQsize = 0

    while True:

        # Get date / time and convert into compatible units
        currentDatetime = localDatetime()# datetime.datetime.now()

        #print(currentDatetime)
        textDayOfWeek = currentDatetime.strftime("%A")
        #print(textDayOfWeek)
        currentTime = currentDatetime.time()
        #print(currentTime)
        textTime = currentTime.strftime("%-I:%M%p")
        #print(textTime)
        timeInSeconds = localTime()# time.time()
        #print("time in seconds: ", timeInSeconds)

        printEn = False
        if (keepAlive % 4000) == 0 and printEn:
            print(textDayOfWeek, textTime, "  Q", pendingZones.qsize())
        keepAlive += 1

        # find timer trigger events and add entries to the queue of zones to be enabled, where a queue entry may be a single zone or a collection of multi zones
        for timer in range(len(timerTable)):
            if timerTable[timer]['Type'] == 'INT':
                if timerTable[timer]['startTime'] == textTime and (timeInSeconds - timerTable[timer]['lastTimeOn']) > 60 * 60 * 24 * (timerTable[timer]['Interval'] - 0.5):
                    timerTable[timer]['lastTimeOn'] = timeInSeconds
                    zoneList = []
                    for zone in range(len(zoneTable)):
                        if zoneTable[zone]['timer']-1 == timer and zoneTable[zone]['wateringTime'] != 0:
                            if zoneTable[zone]['multiZone']:
                                zoneList.append(zone)
                            else:
                                pendingZones.put([zone])
                    if len(zoneList) > 0:
                        pendingZones.put(zoneList)
            else: #timerTable[timer]['Type'] == 'DoW'
                if timerTable[timer]['startTime'] == textTime and timerTable[timer][textDayOfWeek] == 'checked' and (timeInSeconds - timerTable[timer]['lastTimeOn']) > 60 * 60 * 24 * 0.5:
                    timerTable[timer]['lastTimeOn'] = timeInSeconds
                    print("Timer: ", timer, " Active")
                    zoneList = []
                    for zone in range(len(zoneTable)):
                        if zoneTable[zone]['timer'] - 1 == timer and zoneTable[zone]['wateringTime'] != 0:
                            if zoneTable[zone]['multiZone']:
                                zoneList.append(zone)
                            else:
                                pendingZones.put([zone])
                    if len(zoneList) > 0:
                        pendingZones.put(zoneList)

        # Turn on zones, removing one element from the queue, then waiting for the active zone(s) to complete before removing the next element.
        manualWatering = False
        for zone in range(len(zoneTable)):
            if timeInSeconds > zoneTable[zone]['manualStartTime']:
                manualWatering = True
        previousWateringIdle = wateringIdle
        wateringIdle = len(activeZones) == 0 and not manualWatering
        if wateringIdle and not previousWateringIdle: # just finished all watering
            checkRelays()
        if wateringIdle and not pendingZones.empty():
            activeZones = []
            currentZones = pendingZones.get()
            for zone in currentZones:
                activeZones.append((zone, timeInSeconds))
                zoneTable[zone]['on'] = True
            setRelays("as scheduled")
        if not wateringIdle:
            zoneSetToOff = False
            for zone, startTime in activeZones:
                wateringTime = max(MIN_WATERING_TIME, 60 * zoneTable[zone]['wateringTime'] - DOG_WARNING_DURATION * zoneTable[zone]['detectCount'])
                if wateringTime < 60 * zoneTable[zone]['wateringTime'] and previousWateringIdle:
                    print(f"Zone {zoneTable[zone]['name']} adjusted watering time from {60 * zoneTable[zone]['wateringTime']}s to {wateringTime}s")
                if timeInSeconds > startTime + wateringTime:
                    zoneTable[zone]['on'] = False
                    zoneTable[zone]['detectCount'] = 0
                    zoneSetToOff = True
                    activeZones.remove((zone, startTime))
            for zone in range(len(zoneTable)):
                if zoneTable[zone]['multiZone']:
                    wateringTime = autoShutOff['multiZone']
                else:
                    wateringTime = autoShutOff['singleZone']
                if timeInSeconds > zoneTable[zone]['manualStartTime'] + 60 * wateringTime:
                    zoneTable[zone]['on'] = False
                    zoneTable[zone]['manualStartTime'] = END_OF_TIME
                    zoneSetToOff = True
            if zoneSetToOff:
                setRelays("automatically")

        if textDayOfWeek == REPORT_DAY_OF_THE_WEEK and textTime == REPORT_TIME_OF_DAY and os.path.isfile(REPORT_FILE_NAME):
            try:
                sendEmail("Weekly Watering Report", REPORT_FILE_NAME, GORDONS_EMAIL)
                with open(REPORT_FILE_NAME, "r") as reportFile:
                    message_text = reportFile.read()
                try:
                    os.remove(REPORT_FILE_NAME)
                except:
                    print("Error while deleting file ", REPORT_FILE_NAME)
                print("Message Text: \r\n", message_text)
                #sendEmail("Weekly Watering Report", message_text, GORDONS_EMAIL)
            except:
                print("Error e-mailing report file")

    time.sleep(TIMER_SAMPLE_INTERVAL/FAKE_TIME_SCALE)

def runDogMode():
    ''' 
    If config['dogMode'] is True, the RunDogMode thread will turn on the sprinklers set to 
    dog mode for the duration defined by DOG_WARNING_DURATION, clearing the dogWarning global 
    when complete.

    Globals:
        zoneTable (list of dictionaries): data structure for per zone settings.
        config (dictionary): data structure for configuration settings.
        dogWarning (boolean): Indicator if a dog has been detected in the front yard

    Returns:
        Nothing

    Modifies:
        zoneTable, relays
    '''
    global zoneTable
    global dogWarning
    global config

    if config['dogMode']:
        if not dogWarning:
            zoneList = []
            for zone in range(len(zoneTable)):
                if zoneTable[zone]['dogDetectOn'] and not zoneTable[zone]['on']:
                    zoneList.append(zone)
                    zoneTable[zone]['on'] = True
                    zoneTable[zone]['detectCount'] += 1
                    dogWarning = True
            if dogWarning:
                setRelays("for dog detect mode")
                time.sleep(DOG_WARNING_DURATION)
                for zone in zoneList:
                    zoneTable[zone]['on'] = False
                setRelays("for dog detect mode")
                dogWarning = False


def jsonServer():
    ''' 
    The JSON server thread for communicating with the Dog Detector and runs continously after 
    intialization.  jsonServer expects to receive two types of messages, a Dog Warning packet 
    which should trigger the sprinkler sysetem dog mode and a watchdog message which simply 
    requires an ack to indicate the JSON server is alive to the Dog Detector.
    '''
    HOST = '0.0.0.0'  #
    PORT = 2579  #

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as json_server:
        json_server.bind((HOST, PORT))
        while True: # repeatildy open connections
            json_server.listen()
            conn, addr = json_server.accept()
            with conn:
                print('Connected by', addr)
                while True:
                    data = conn.recv(1024)
                    if data:
                        data_packets = [packet + b"}" for packet in data.split(b"}") if packet]
                        print("Data Packets: ", data_packets)
                        for index in range(len(data_packets)):
                            received = json.loads(data_packets[index])
                            if received["Type"] == "Dog Warning":
                                response = json.dumps({"Type": "Dog Warning Ack"})
                                conn.sendall(response.encode('utf-8'))
                                try:
                                    runDogModeThread = Thread(target=runDogMode)
                                    runDogModeThread.start()
                                except:
                                    print("Error: unable to start Run Dog Mode thread")
                            elif received["Type"] == "WatchDog":
                                response = json.dumps({"Type": "WatchDog Ack"})
                                conn.sendall(response.encode('utf-8'))
                            else:
                                response = json.dumps({"Type": "Unknown Message Ack"})
                                conn.sendall(response.encode('utf-8'))
                                print("Unknown message type")
                                print(received)
                    if not data:
                        break

def watchDogPetter():
    ''' 
    watchDogPetter is a true watchdog in the embedded sense.  The use of watchdogs in Linux operating systtems
    takes on a different context and purpose.  In Linux severs the overriding purpose of the watchdog is to 
    prevent bricking of the server itself.  As a result the watchdog has been created so that critical processes
    such as network communication are monitored and the system is hard reset if those services cease to operate.
    Verifying operation via process ID is not effective for a multithreaded application, such as this one where 
    individual threads within the applicaiton need to be monitored.  As a result the hardware watchdog is called 
    through the watchdog driver.  Via an os call.  This is the same hardware which is called through the watchdog
    deamon (very unforntuately named watchdog as there is namespace colisions between the configuration of the 
    deamon and the driver, for which I have not found a definitive reference).  This implimentation does not 
    envolk the watchdog deamon as the hardware does not support competeing resources controlling the hardware.
    WARNING:  Be very careful to understand any changes made to the configuration of the driver and accidentally
    involking the watchdog deamon as this will prevent this thread from operating.

    Globals:
        SUDO_PASSWORD (Constant String): sudo password for pi.
        WATCH_DOG_PET_INTERVAL (Constant Int): Time between petting the dog.
        keepAlive (Int): Timer counter indicating the timer thread is operating

    Returns:
        Nothing

    Modifies:
        zoneTable, relays
    '''
    global SUDO_PASSWORD
    global WATCH_DOG_PET_INTERVAL
    global keepAlive

    command = 'sh -c "echo \'.\' >> /dev/watchdog"'
    lastKeepAlive = -1
    bufferedKeepAlive = -1
    petCounter = 1

    while True:
        if keepAlive != lastKeepAlive:
            p = os.system('echo %s|sudo -S %s' % (SUDO_PASSWORD, command))
            petCounter += 1
        if petCounter % int(3 * TIMER_SAMPLE_INTERVAL/WATCH_DOG_PET_INTERVAL) == 0:
            lastKeepAlive     = bufferedKeepAlive
            bufferedKeepAlive = keepAlive
        time.sleep(WATCH_DOG_PET_INTERVAL)

if __name__ == "__main__":
    '''
    Initialize data structures and lauch threads.
    '''

    relays = RelayController.relayCont(relaysStackAddressList)
    relays.verbose(1)
    relays.open()

    loadState()
    #print(zoneTable)
    if DEBUG:
        for timer in range(len(timerTable)):
            timerTable[timer]['lastTimeOn'] = 0

    try:
        saveStateThread = Thread(target=saveState)
        saveStateThread.start()
        print("Save State Thread: ", saveStateThread)
    except:
         print("Error: unable to start save state thread")

    try:
        timersThread = Thread(target=timerThread)
        timersThread.start()
        print("Timers Thread: ", timersThread)
    except:
         print("Error: unable to start timers thread")

    try:
        jsonServerThread = Thread(target=jsonServer)
        jsonServerThread.daemon = True
        jsonServerThread.start()
        print("Jason Sever Thread: ", jsonServerThread)
    except:
         print("Error: unable to start JSON server thread")

    if WATCH_DOG_ENABLE and piHost:
        try:
            watchDogPetterThread = Thread(target=watchDogPetter)
            watchDogPetterThread.daemon = True
            watchDogPetterThread.start()
            print("Watch Dog Petting Thread: ", watchDogPetterThread)
        except:
            print("Error: unable to start Watch Dog Petting thread")

    app.run(host='0.0.0.0', debug=True, use_reloader=False)

