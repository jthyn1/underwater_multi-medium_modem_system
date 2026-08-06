#!/usr/bin/python

from socket import socket, AF_INET, SOCK_STREAM, IPPROTO_TCP, TCP_NODELAY
import sys
import time
import threading
import json
import queue
import struct
import random
import logging
import os
import os.path
import string
import select
import traceback
import io
import shlex

FRAMESIZE = 640
VERSION_SERVER_PORT = 39484
VERSION_SERVER_TIMEOUT_SECONDS = 5

# Do not execute on modem hardware
if not os.path.exists("/etc/PopotoSerialNumber.txt"):
    WAVE_FILE_SUPPORTED = True

    try:
        import scipy.io.wavfile as scipy_io_wavfile
    except:
        print("For file to file unit tests please install scipy")
        WAVE_FILE_SUPPORTED = False
    try:
        import wave
    except:
        print("For file to file unit tests please install wave")
        WAVE_FILE_SUPPORTED = False
    try:
        import numpy as np
    except:
        print("For file to file unit tests please install numpy")
        WAVE_FILE_SUPPORTED = False

    try:
        from progressbar import Bar,  ETA, FileTransferSpeed, Percentage, ProgressBar
    except:
        print("For file to file unit tests please install progressbar")
        WAVE_FILE_SUPPORTED = False


PCMLOG_OFFSET = 2
CAPTURES_PARTITION = '/dev/mmcblk0p3'
CAPTURES_PARTITION_DEVICE = '/dev/mmcblk0'
CAPTURES_PARTITION_NUMBER = 3
FULLSD_FILE = '/etc/sd_full'

SUPERFRAME_SIZE = 2048
'''The size of the superframe for streaming'''

class popoto:
    '''
    An API for the Popoto Modem product

    This class can run on the local Popoto Modem,  or can be run
    remotely on a PC.

    All commands are sent via function calls, and all JSON encoded responses and status
    from the modem are enqueued as Python objects in the reply queue.

    In order to do this, the class launches a processing thread that looks for replies
    decodes the JSON and adds the resulting python object into the reply queue.

    The Popoto class requires an IP address and port number to communicate with the Popoto Modem.
    This Port number corresponds to the base port of the modem application.


    '''

    ROBOT_LIBRARY_SCOPE = 'GLOBAL'

    def __init__(self, ip='localhost', basePort=17000, logname=None):
        self.logger = logging.getLogger(logname)
        self.logger.info("Popoto Init Called")
        self.pcmplayport = basePort + 5
        self.pcmioport = basePort + 3
        self.pcmlogport = basePort + 2
        self.dataport = basePort + 1
        self.cmdport = basePort
        self.rawTerminal = False
        self.quiet = 0
        self.logger.info("Opening Command Socket")
        self.cmdsocket = socket(AF_INET, SOCK_STREAM)
        self.cmdsocket.connect((ip, basePort))
        self.cmdsocket.settimeout(20)
        self.verbose = 2
        self.SampFreq = 102400
        self.pcmplaysocket = 0
        self.recByteCount = 0
        self.ip = ip
        self.is_running = True
        self.fp = None
        self.fileLock = threading.Lock()
        self.logger.info("Starting Command Thread")

        # Dictionary to hold JSON message intercept handlers.
        # Each key maps to a tuple: (callback, user_arg)
        self._intercept_handlers = {}

        self.rxThread = threading.Thread(target=self.RxCmdLoop, name="CmdRxLoop", daemon=True)
        self.rxThread.start()
        self.replyQ = queue.Queue()
        self.datasocket = None
        self.logger.info("Starting pcmThread")
        self.intParams = {}
        self.floatParams = {}
        self.paramsList = []
        self.varcache = {}
        self.cachingEnabled = True
        self.isRemoteCmd = False
        self.remoteCommandAck = -1
        self.remotePshellEnabled = False
        self.remotePshellCommandQueue = queue.Queue()
        self.getAllParameters()
        self.remoteCommandHandler = None
        self.CurrentCommandRemote = False
        self.pcmiosocket = None
        self.MapPayloadModesToRates = [80, 5120, 2560, 1280, 640, 10240]
        self.first_recording = True
        self.running_on_modem = self.check_running_on_modem()

    def prepareFilesystem(self):
        if self.running_on_modem:
            self.expandCapturesPartition()
            self.mountCapturesPartition()



    def setRawTerminal(self):
        self.rawTerminal = True

    def setANSITerminal(self):
        self.rawTerminal = False

    def setRemoteCommandHandler(self, obj):
        self.remoteCommandHandler = obj
        return

    def register_message_handler(self, message_type, callback, user_arg=None):
        """
        Registers a callback function to intercept JSON messages that contain a specific key.
        The callback will be called with two arguments: the intercepted message (as a dict)
        and the user_arg provided at registration time.

        :param message_type: The JSON key that will trigger the callback.
                             For example, if you want to intercept messages that include the key
                             "MyCustomType", pass that string here.
        :param callback: A function that will be called with the intercepted message and user_arg.
        :param user_arg: An additional argument that will be passed to the callback.
        """
        self._intercept_handlers[message_type] = (callback, user_arg)
        self.logger.info("Registered handler for message type '%s' with user_arg=%s",
                         message_type, user_arg)

    def unregister_message_handler(self, message_type):
        """
        Unregisters the callback function for a given JSON message type.

        :param message_type: The JSON key for which the callback should be removed.
        """
        if message_type in self._intercept_handlers:
            del self._intercept_handlers[message_type]
            self.logger.info("Unregistered handler for message type '%s'", message_type)
        else:
            self.logger.warning("No handler registered for message type '%s'", message_type)


    def check_running_on_modem(self):
        if os.path.exists('proc/cpuinfo'):
            with open('/proc/cpuinfo') as file:
                cpuinfo = file.read()
        else:
            cpuinfo = "no_cpuinfo_file"
        on_modem = os.path.exists("/etc/PopotoSerialNumber.txt") and (
            "ARM926EJ-S rev 5 (v5l)" in cpuinfo or "fp asimd evtstrm aes pmull sha1 sha2 crc32 cpuid" in cpuinfo)
        self.logger.debug(f'Running on PMM hardware: {on_modem}')
        return on_modem

    def send(self, message):
        """
        The send function is used to send a command  with optional arguments to Popoto as
        a JSON string

        :param      message:  The message contains a Popoto command with optional arguments
        :type       message:  string
        """
        args = message.split(' ', 1)

        # Break up the command and optional arguements around the space
        if len(args) > 1:
            command = args[0]
            arguments = args[1]
        else:
            command = message
            arguments = "Unused Arguments"

        if (self.isRemoteCmd):

            if (self.remoteCommandAck):
                command = "RemoteCommandWithAck"
            else:
                command = "RemoteCommand"

            arguments = message + ' ' + arguments

        # Build the JSON message
        message = "{ \"Command\": \"" + command + \
            "\", \"Arguments\": \"" + arguments + "\"}"
        if (self.verbose > 0):
            self.logger.info(message)
        # Send the message to the command socket
        try:
            if self.verbose > 2:
                self.logger.info(
                    "Port:" + str(self.cmdport) + " >>>> " + message)
            message = message + '\n'
            self.cmdsocket.sendall(message.encode())
        except Exception as e:
            self.logger.error(e)
            self.logger.error("Port:" + str(self.cmdport) +
                              " >>>> " + "SEND ERROR")

    def sendRemoteStatus(self, message):
        status = "RemoteStatus " + message
        self.drainReplyQ()

        self.send(status)

        self.waitForSpecificReply("Alert", "TxComplete", 20)

    def setRemoteCommand(self, AckFlag):
        '''
            Sets up the python API to send a command to a remote modem over acoustic
            channels.  The RemoteNode variable controls which modem to send the command to

        '''
        self.isRemoteCmd = True
        if (AckFlag):
            self.remoteCommandAck = True
        else:
            self.remoteCommandAck = False

    def setLocalCommand(self):
        '''
            Sets up the python API to send a command to the local modem
        '''
        self.isRemoteCmd = False
        self.remoteCommandAck = False

    def drainReplyQ(self):
        """
        This function reads and dumps any data that currently resides in the
        Popoto reply queue.  This function is useful for putting the replyQ in a known
        empty state.
        """
        while self.replyQ.empty() == False:
            self.logger.info(self.replyQ.get())

    def drainReplyQquiet(self):
        """
        This function reads and dumps any data that currently resides in the
        Popoto reply queue.  This function is useful for putting the replyQ in a known
        empty state.
        """
        while self.replyQ.empty() == False:
            self.replyQ.get()

    def sendRemotePshell(self, command):
        message = "{ \"Command\": \"remotePshellCommand\", \"Arguments\": \"" + command +"\" }"
        try:
            testJson = json.loads(message)
            self.logger.info("Sending " + message)
            message = message + '\n'
            self.cmdsocket.sendall(message.encode())
        except json.JSONDecodeError:
            self.logger.error("Invalid JSON message: ", message)
        except OSError:
            self.logger.error("Could not send message. Please reconnect.")


    def sendRemotePshellAck(self, command):
        message = "{ \"Command\": \"remotePshellCommandAck\", \"Arguments\": \"" + command +"\" }"
        try:
            testJson = json.loads(message)
            self.logger.info("Sending " + message)
            message = message + '\n'
            self.cmdsocket.sendall(message.encode())
        except json.JSONDecodeError:
            self.logger.error("Invalid JSON message: ", message)
        except OSError:
            self.logger.error("Could not send message. Please reconnect.")


    def waitForReply(self, Timeout=10):
        """
        waitForReply is a method that blocks on the replyQ until either a reply has been
        received or a timeout (in seconds) occurs.

        :param      Timeout:  The timeout
        :type       Timeout:  { type_description }
        """
        try:
            reply = self.replyQ.get(True, Timeout)
        except:
            reply = {"Timeout":0}
        return reply

    def waitForSpecificReply(self, Msgtype, value, Timeout=10):
        """
        waitForReply is a method that blocks on the replyQ until either a reply has been
        received or a timeout (in seconds) occurs.

        :param      Timeout:  The timeout
        :type       Timeout:  { type_description }
        """
        done = 0
        start = time.time()
        try:
            while (done == 0 ):
                reply = self.replyQ.get(True, Timeout)
                if(Msgtype in reply ):
                    if(value != None):
                        if(reply[Msgtype] == value):
                            done =1
                        elif type(reply[Msgtype]) == str:
                            if(value in reply[Msgtype]):
                                done = 1
                    else:
                        done = 1
                elapsedTime = time.time() - start
                if(elapsedTime > Timeout):
                    reply = {"Timeout": 0}
                    return reply
        except:
            reply = {"Timeout":0}
        return reply

    def startRx(self):
        """
        startRx places Popoto modem in receive mode.
        """
        self.send('Event_StartRx')

    def calibrateTransmit(self):
        """
        calibrateTransmit send performs a calibration cycle on a new transducer
        to allow transmit power to be specified in watts.  It does this by sending
        a known amplitude to the transducer while measuring voltage and current across
        the transducer.  The resulting measured power is used to adjust scaling parameters
        in Popoto such that future pings can be specified in watts.
        """
        self.setValueF('TxPowerWatts', 1)
        self.send('Event_startTxCal')

    def transmitJSON(self, JSmessage):
        """
        The transmitJSON method sends an arbitrary user JSON message for transmission out the
        acoustic modem.

        :param      JSmessage:  The Users JSON message
        :type       JSmessage:  string
        """

        if type(JSmessage) != dict:
            try:
                JSmessage = json.loads(JSmessage)
            except json.JSONDecodeError:
                self.logger.error("Failed to parse JSON:\n\r" + traceback.format_exc())
                return

        if "Payload" in JSmessage.keys() and "Data" in JSmessage["Payload"].keys():
            data: list = JSmessage["Payload"]["Data"]
        elif "ClassUserID" in JSmessage.keys():
            data = []
        else:
            self.logger.error('Transmitted JSON must be in the format: {"Payload": {"Data": []}}')
            return

        # For data with a length of > 255, enable streaming mode and split the data into packets for sending quickly
        if len(data) > 255:
            self.streamBytes(data)
            return

        # Format the user JSON message into a TransmitJSON message for Popoto
        message = "{ \"Command\": \"TransmitJSON\", \"Arguments\": " + json.dumps(JSmessage) + " }"

        # Verify the JSON message integrity and send along to Popoto
        try:
            self.logger.info("Sending " + message)
            message = message + '\n'
            self.cmdsocket.sendall(message.encode())
        except OSError:
            self.logger.error("Could not send message. Please reconnect.")

    def mariaCommand(self, JSmessage):
        """
        MARIA command builder with automatic type parsing.
        If there is exactly one argument, "Arguments" will be that item directly.
        If more than one, "Arguments" will be a list.
        """

        # Split "dest:rest"
        try:
            _dest, _func_and_args = JSmessage.split(':', 1)
        except ValueError:
            self.logger.err("Invalid MARIA command format (missing ':'): " + JSmessage)
            return

        parts = _func_and_args.strip().split(' ', 1)
        _func = parts[0]
        _args_str = parts[1].strip() if len(parts) > 1 else ''

        msg_dict = {
            "Command": "MARIACommand",
            "Destination": _dest,
            "Function": _func
        }

        # Handle arguments
        if _args_str != '':
            stripped = _args_str.lstrip()

            # --- Case 1: user is clearly trying to send raw JSON ({...} or [...]) ---
            if stripped.startswith('{'):
                try:
                    parsed = json.loads(stripped)
                    msg_dict["Arguments"] = parsed
                except json.JSONDecodeError as e:
                    self.logger.err(
                        f"Invalid JSON for Arguments: {_args_str} ({e})"
                    )
                    return

            else:
                # --- Case 2: normal "func arg1 arg2 ..." style → type-interpret tokens ---
                tokens = shlex.split(_args_str)
                parsed_args = []

                for tok in tokens:
                    low = tok.lower()

                    # bool
                    if low == "true":
                        parsed_args.append(True)
                        continue
                    if low == "false":
                        parsed_args.append(False)
                        continue

                    # null
                    if low == "null":
                        parsed_args.append(None)
                        continue

                    # int
                    try:
                        parsed_args.append(int(tok))
                        continue
                    except ValueError:
                        pass

                    # float
                    try:
                        parsed_args.append(float(tok))
                        continue
                    except ValueError:
                        pass

                    # fallback string
                    parsed_args.append(tok)

                # Single vs multiple argument behavior
                if len(parsed_args) == 1:
                    msg_dict["Arguments"] = parsed_args[0]
                else:
                    msg_dict["Arguments"] = parsed_args


        # Serialize and send
        try:
            message = json.dumps(msg_dict)
            self.logger.info("Sending " + message)
            self.cmdsocket.sendall((message + "\n").encode())
        except Exception as e:
            self.logger.err("Invalid JSON message: " + JSmessage + " (" + str(e) + ")")

    def setActiveChannels(self, line):
        """
        The setActiveChannels method enables JACK Pcm streaming channels for recording

        :param      Channels:  The Users desired recording channels
        :type       Channels:  string of space separated ints, or the string "all"
        """
        try:
            if line.strip().lower() == 'all':
                bitmask = 255
            else:
                channels = line.strip().split()
                bitmask = 0
                for c in channels:
                    ch = int(c)
                    bitmask |= (1 << (ch - 1))
            
            cmd = { "Command": "SetValue", "Arguments": f"MARIA_Popoto_InputChannelMask {bitmask} 0"}
            message = json.dumps(cmd)
            testJson = json.loads(message)
            self.logger.info("Sending " + message)
            message = message + '\n'
            self.cmdsocket.sendall(message.encode())

        except:
            print("Unable to parse channels!")

    def getVersion(self):
        """
        Retrieve Popoto component versions from the modem's data server.
        """
        try:
            versions = self._fetch_versions_from_modem()
            if versions:
                return versions
            self.logger.warning(
                "Data server returned no data; falling back to legacy getVersion command."
            )
        except Exception as exc:
            self.logger.error(
                "Data server lookup failed; falling back to legacy getVersion command: %s",
                exc,
            )

        # Legacy behavior: request version via popoto_app command socket.
        self.send('getVersion')
        return []

    def _fetch_versions_from_modem(self):
        """
        Connect to the modem-hosted data server to fetch version data.
        """
        try:
            with socket(AF_INET, SOCK_STREAM) as version_socket:
                version_socket.settimeout(VERSION_SERVER_TIMEOUT_SECONDS)
                version_socket.connect((self.ip, VERSION_SERVER_PORT))
                # Payload content is irrelevant; presence of a connection triggers a response.
                version_socket.sendall(b"getVersion\n")
                reply_chunks = []
                while True:
                    data = version_socket.recv(4096)
                    if not data:
                        break
                    reply_chunks.append(data)
        except OSError as exc:
            self.logger.error(
                "Failed to reach modem data server at %s:%s: %s",
                self.ip,
                VERSION_SERVER_PORT,
                exc,
            )
            return []

        payload = b"".join(reply_chunks).decode("utf-8").strip()
        if not payload:
            self.logger.error(
                "Data server at %s:%s returned no data", self.ip, VERSION_SERVER_PORT
            )
            return []

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            self.logger.error(
                "Invalid JSON from data server at %s:%s: %s",
                self.ip,
                VERSION_SERVER_PORT,
                exc,
            )
            return []

    def sendPrecisionRange(self, power=.1):
        """
        Send a command to Popoto to initiate a precision ranging cycle to another modem

        :param      power:  The power in watts
        :type       power:  number
        """
        self.setValueF('TxPowerWatts', power)
        self.setValueI('CarrierTxMode', 0)
        self.send('Event_sendPrecisionRanging')

    def sendRange(self, power=.1):
        """
        Send a command to Popoto to initiate a ranging cycle to another modem

        :param      power:  The power in watts
        :type       power:  number
        """
        self.setValueF('TxPowerWatts', power)
        self.setValueI('CarrierTxMode', 0)
        self.send('Event_sendRanging')

    def recordStartTarget(self, filename, duration, capturesdir='/captures'):
        """
        Initiate recording acoustic signal .rec data to the local SD card.
        Recording is passband if  Popoto 'RecordMode' is 0
        Recording is baseband if  Popoto 'RecordMode' is 1

        :param      filename:  The filename on the local filesystem with path
        :type       filename:  string
        :param      duration:  The duration in seconds for continuous record to split-up
                                files with autonaming.  Typical value is 60 for 1 minute files.
        :type       duration:  number
        """

        if (filename[:10] != capturesdir + '/') and os.path.exists(capturesdir):
            filename = capturesdir + '/' + filename
            self.logger.info("File recording in" + capturesdir + "/ directory: " + filename)

        self.send('StartRecording {} {}'.format(filename, duration))

    def getRecordingStatus(self):
        '''
        Request the current status and key information of the current recording session, in JSON Format.
        '''
        self.send('GetRecordingStatus')

    def expandCapturesPartition(self):
        '''Expands the partition at {CAPTURES_PARTITION} to fill the remaining space on the drive.'''
        trailing_space_available = os.system(f"parted {CAPTURES_PARTITION_DEVICE} print free | tail -n 2 | grep -q 'Free Space'")
        if trailing_space_available == 0:
            print(f"Filesystem for {CAPTURES_PARTITION_DEVICE} has trailing unpartitioned space. Resizing {CAPTURES_PARTITION} to fill remaining space (this may take some time)...")
            os.system('umount /captures &> /dev/null')
            if os.system(f'parted -s -a opt {CAPTURES_PARTITION_DEVICE} "resizepart {CAPTURES_PARTITION_NUMBER} 100%"') == 0 and os.system(f'resize2fs {CAPTURES_PARTITION}') == 0:
                print("Filesystem resize complete.")
            else:
                print("Error resizing filesystem! /captures partition may be very small.")
        else:
            print(f"Filesystem for {CAPTURES_PARTITION_DEVICE} has no trailing unpartitioned space. Not resizing {CAPTURES_PARTITION}.")

    def mountCapturesPartition(self):
        '''Mounts /dev/mmcblk0p3 at /captures/'''
        if os.path.isfile('/captures'):
            if os.path.getsize('/captures') == 0:
                os.remove('/captures')
            else:
                print("ERROR: /captures is a file, not a directory! Moving to '/captures.file'...")
                os.rename('/captures', '/captures.file')

        if not os.path.isdir('/captures'):
            os.mkdir('/captures')

        os.system(f'umount /captures &> /dev/null')
        if os.system(f'fsck -y {CAPTURES_PARTITION}') & 4 != 0:
            print(f"WARNING: repairing {CAPTURES_PARTITION} failed!")

        rc = os.system(f'/bin/mount {CAPTURES_PARTITION} /captures > /dev/null')
        if rc != 0:
            print("WARNING: /captures directory partition failed to mount. You may not have much storage space to record to.")

    def recordStopTarget(self):
        """
        Turn off recording to local SD card
        """
        self.send('StopRecording')

    def playStartTarget(self,filename, scale):
        """
        Play a PCM file of 32bit IEEE float values out the transmitter
        Playback is passband if  Popoto 'PlayMode' is 0
        Playback is baseband if  Popoto 'PlayMode' is 1

        :param      filename:  The filename of the pcm file on the SD card
        :type       filename:  string
        :param      scale:     The transmitter scale value 0-10; higher numbers result in
                                higher transmit power.
        :type       scale:     number
        """
        if filename[:10] != '/captures/' and os.path.exists("/captures"):
            filename = '/captures/' + filename
            self.logger.info("File playing from /captures/ directory: " + filename)
        self.logger.info ("Playing {} at Scale {}".format(filename, scale))
        self.send('StartPlaying {} {}'.format(filename, scale))

    def playStopTarget(self):
        """
        End playout of stored PCM file through Popoto transmitter
        """
        self.send('StopPlaying')

    def set(self, Element, value):
        """
        Sets a value of a Popoto variable

        :param      Element:  The name of the variable to be set
        :type       Element:  string
        :param      value:    The value
        :type       value:    integer or float
        """
        if self.cachingEnabled:
            if self.varcache.get(Element) != value:
                self.send('SetValue {} {} 0'.format(Element, value))
            self.varcache[Element] = value
        else:
            self.send('SetValue {} {} 0'.format(Element, value))

    def get(self, Element):
        """
        gets a value of a Popoto variable

        :param      Element:  The name of the variable to be set
        :type       Element:  string
        :param      value:    The value
        :type       value:    integer or float
        """
        self.send('GetValue {} 0'.format(Element))

    def setValueI(self, Element, value):
        """
        Sets an integer value of a Popoto integer variable

        :param      Element:  The name of the variable to be set
        :type       Element:  string
        :param      value:    The value
        :type       value:    integer
        """
        self.set(Element, value)

    def setValueF(self, Element, value):
        """
        Sets a 32bit float value of a Popoto float variable

        :param      Element:  The name of the variable to be set
        :type       Element:  string
        :param      value:    The value
        :type       value:    float
        """
        self.set(Element, value)

    def getValueI(self, Element):
        """
        Gets an integer value of a Popoto integer variable

        :param      Element:  The name of the variable to be retreived
        :type       Element:  string
        :returns    value:    The value
        :type       value:    integer
        """
        self.get(Element)

    def getValueF(self, Element):
        """
        Gets the 32bit floating value of a Popoto float variable

        :param      Element:  The name of the variable to be retreived
        :type       Element:  string
        :returns    value:    The value
        :type       value:    float
        """
        self.get(Element)


    def tearDownPopoto(self):
        """
        The tearDownPopoto method provides a graceful exit from any python Popoto script

        """
        done=0
        self.getVersion()
        self.is_running = False
        time.sleep(1)

    def setRtc(self, clockstr):
        """
        Sets the real time clock.

        :param      clockstr:  The clockstr contains the value of the date in string
                                format YYYY.MM.DD-HH:MM;SS
                                Note: there is no error checking on the string so make it right
        :type       clockstr:  string
        """
        self.send('SetRTC {}'.format(clockstr))

    def getRtc(self):
        """
        Gets the real time clock date and time.

        :returns     clockstr:  The clockstr contains the value of the date in string
                                format YYYY.MM.DD-HH:MM;SS
        :type       clockstr:   string
        """
        self.send('GetRTC')

    def __del__(self):
        # Destructor
        done = 0

        # Read all data out of socket
        self.is_running = False


    def playPcmLoop(self, inFile, scale, bb):
        """
        playPcmLoop
        Play passband/baseband rec file (Note file must be at least 4 seconds long)
        :param      inFile:  In file
        :type       inFile:  string
        :param      bb:      selects passband (0) or baseband (1) data
        """
        self.pcmplaysocket=socket(AF_INET, SOCK_STREAM)
        self.pcmplaysocket.connect((self.ip, self.pcmplayport))
        self.pcmplaysocket.settimeout(1)
        if(self.pcmplaysocket == None):
            self.logger.error("Unable to open PCM Log Socket")
            return
        # Set mode to either passband-0 or baseband-1
        self.setValueI('PlayMode', bb)

        # Start the play
        self.send('StartNetPlay 0 0')

        # Open the file for playing
        fpin  = open(inFile, 'r')
        if(fpin == None):
            self.logger.error("Unable to Open {} for Reading")
            return
        s_time = time.time()
        sampleCounter = 0
        if(bb):
            SampPerSec = 10240 *2
        else:
            SampPerSec = 102400
        gain = struct.pack('f', scale)
        if('rec' in inFile[-4:] ):
            self.logger.info('Playing a rec file')
            readLen = 642*4
            startOffset = 8
        else:
            self.logger.info('Playing a raw file')
            readLen = 640*4
            startOffset = 0

        Done = 0
        while Done == 0:
            # Read socket of pcm data
            fdata = fpin.read(readLen)
            if(len(fdata) < readLen):
                self.logger.info('Done Reading File')
                Done = 1
            fdata = gain + gain + fdata[startOffset:]
            StartSample = sampleCounter
            while(sampleCounter == StartSample and len(fdata) > 8):
                try:
                    self.pcmplaysocket.send(fdata) # Send data over socket
                    sampleCounter += (len(fdata)-8)

                except:
                    self.logger.error('Waiting For Network SampleCount {}'.format(sampleCounter))
                    self.logger.error(sys.exc_info()[0])
                    Done = 1

        duration = sampleCounter / (4*SampPerSec)  #  Bytes to Floats->seconds
        self.logger.info('Duration {}'.format(duration))

        while(time.time() < s_time+duration):
            time.sleep(1)

        # Terminate play
        self.send('Event_playPcmQueueEmpty')

        self.logger.debug("Exiting PCM Loop")
        self.pcmplaysocket.close()
        fpin.close()


    def recPcmLoop(self, outFile, duration, bb):
        """
        recPcmLoop records passband/baseband rec file for duration seconds.
        This function also returns a vector of timestamps in pcmCount and a vector of
        HiGain_LowGain flags 0=lo,1=hi which indicate which A/D
        channel was selected on a frame basis

        Code sets baseband mode as selected on input, but changes back to pass
        band mode on exit.  Base band recording and normal modem function are
        mutually exclusive, as they share the Modem's Digital up converter.

        :param      outFile:   The output filename with path
        :type       outFile:   string
        :param      duration:  The duration of recording in seconds
        :type       duration:  number
        :param      bb:        passband or baseband selection
        :type       bb:        number 0/1 passband/baseband
        """
        self.logger.info('Opening ' + outFile)
        # Open and configure streaming port
        self.pcmplaysocket=socket(AF_INET, SOCK_STREAM)
        self.pcmplaysocket.connect((self.ip, self.pcmlogport))
        self.pcmplaysocket.settimeout(1)

        # Set mode to either passband-0 or baseband-1
        self.setValueI('RecordMode', bb)
        if(bb == 1):
            duration = duration * 10240 * 2   # Baseband rate 10240 Cplx samples /sec
        else:
            duration = duration * 102400


        if(self.pcmplaysocket == None):
            self.logger.error("Unable to open PCM Log Socket")
            self.setValueI('RecordMode', 0)
            return

        # Open the recording file
        fpout = open(outFile,'w')
        if(fpout == None):
            self.logger.error("Unable to Open {} for Writing")
            self.setValueI('RecordMode', 0)
            return

        self.recByteCount = 0
        Done = 0
        while Done == 0:
            # Read socket
            try:
                fromRx=self.pcmplaysocket.recv(642*4) # Read the socket
                if fpout != None:
                    fpout.write(fromRx)     # write the data
                self.recByteCount = self.recByteCount + len(fromRx)-2
                if (self.recByteCount >= duration*4):
                    Done=1
                FrameCounter = FrameCounter + 1
                if FrameCounter > 80:
                    self.logger.debug('.')
                    FrameCounter = 0
            except OSError:
                self.logger.debug("OSError while recording.")
                continue
            except IOError:
                self.logger.debug("IOError while writing to file.")
                continue



        self.logger.info("Exiting PCM Loop")
        self.pcmplaysocket.close()
        fpout.close()
        self.setValueI('RecordMode', 0)

    def statReport(self, line):
        if(self.CurrentCommandRemote):
            self.sendRemoteStatus(line)
            self.logger.info(line)
        else:
            self.logger.info(line)

    def streamBytes(self, data):
        METHOD = "TransmitJSON"
        # Set up the data socket
        if (self.datasocket == None):
            self.datasocket = socket(AF_INET, SOCK_STREAM)

            self.datasocket.connect((self.ip, self.dataport))
            self.datasocket.settimeout(10)
            self.datasocket.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)

            if (self.datasocket == None):
                self.statReport("Unable to open data Socket")
                return

        # Make sure the data is bytes, not str
        if type(data) == str:
            data = data.encode()

        # Get the length of the file or stream
        if type(data) == io.BufferedReader:
            data.seek(0, io.SEEK_END)
            total_byte_count = data.tell()
            data.seek(0)

            def get_next_packet(start, length):
                data.seek(start)
                return bytes(data.read(length))
        else:
            total_byte_count = len(data)
            def get_next_packet(start, length):
                return bytes(data[start:start + length])

        superframe_size = min(SUPERFRAME_SIZE, total_byte_count)  # Maximum super frame size
        # self.setValueI('StreamingTxLen', superframe_size)

        total_bytes_sent = 0
        PacketsTransmitted = 0
        Msg = {}
        Msg['Payload'] = {}

        while(total_bytes_sent < total_byte_count):
            superframe_bytes_sent = 0
            while(superframe_bytes_sent < superframe_size):
                readLen = superframe_size - superframe_bytes_sent

                if METHOD == 'TransmitJSON' and (readLen > 255):
                    readLen = 255

                packet = get_next_packet(total_bytes_sent, readLen)

                Msg['Payload']["Data"] = list(packet)

                try:
                    if METHOD == 'TransmitJSON':
                        sendbytes = b"{ \"Command\": \"TransmitJSON\", \"Arguments\": " + json.dumps(Msg).encode() + b" }\n"
                        self.cmdsocket.sendall(sendbytes)
                    elif METHOD ==  'Streaming':
                        self.datasocket.send(packet)
                except:
                    self.logger.error("ERROR SENDING ON DATA SOCKET")

                total_bytes_sent += len(packet)
                superframe_bytes_sent += len(packet)
                self.logger.info('Bytes Uploaded ' + str(total_bytes_sent))
                # WAIT FOR COMPLETE
            PacketsTransmitted += 1

            if((total_byte_count - total_bytes_sent) < superframe_size):
                superframe_size = total_byte_count-total_bytes_sent

        # Compute the number of seconds to wait for a Complete message
        # Based on the payload mode.  Up it by factor of 10 to account
        # for headers etc, and the "timeout" nature of a timeout.
        # in the normal case we won't wait this long, as the complete message
        # will arrive.
        self.get("PayloadMode")
        PayloadMode = self.waitForSpecificReply("PayloadMode", None)
        if PayloadMode != {"Timeout": 0}:
            PayloadMode = int(PayloadMode["PayloadMode"])
        else:
            # Assume 80bps to be safe
            PayloadMode = 0

        timeout = 10*(total_byte_count * 8) / self.payLoadModeToBitRate(PayloadMode)

        self.waitForSpecificReply("Alert", 'TxComplete', timeout)

    def streamUpload(self, filename, power, PayloadMode =1):
        """
        streamUpload Upload a file for acoustic transmission

        :param      filename:  The filename to be sent with path
        :type       filename:  string
        :param      power:     The desired power in watts
        :type       power:     number
        """
        if(self.isRemoteCmd):
            self.send('upload ' + filename + ' ' + str(power))
            return

        if os.path.isfile(filename) and os.access(filename, os.R_OK):
            self.logger.debug("File exists and is readable")
            nbytes = os.path.getsize(filename)
            if(nbytes == 0):
                self.statReport("ZERO LENGTH FILE: NOT UPLOADING")
                return
            self.logger.debug("File is %d bytes" % nbytes )
        else:
            self.statReport ("Either the file is missing or not readable")
            return
        self.logger.debug("OK")
        self.statReport ("OK")
        # All good with the file lets upload
        done = 0
        while(done == 0):
            try:
                self.replyQ.get(False)
            except queue.Empty:
                done = 1

        self.setValueI('TCPecho', 0)
        self.setValueI('ConsolePacketBytes', 256)
        self.setValueI('ConsoleTimeoutMS', 100)
        self.setValueI('PayloadMode', PayloadMode)
        self.setValueF('TxPowerWatts', power)

        done = 0
        while(done == 0):
            time.sleep(.1)
            resp = self.replyQ.get()
            self.logger.debug("Got a response")
            self.logger.debug(resp)
            if('PayloadMode' in resp['Info']):
                done = 1

        # Read each character and send it to the socket
        self.drainReplyQquiet()

        with open(filename,'rb') as f:
            self.streamBytes(f)

        self.logger.info("Upload Complete")
        self.setValueI('PayloadMode', 0)
        self.setValueI('StreamingTxLen', 0)

    def streamDownload(self, filename, remotePowerLevel):
        """
        streamDownload Upload a file for acoustic transmission

        :param      filename:  The filename to be recieved with path.   The local file downloaded
                                will have the .download extension appended.

        :param      remotePowerLevel: if the remote power level is specified,
                                        then a remote upload command is issued to the
                                        remote device.

        :type       filename:  string
        """
        #clear reception queue
        TimeoutSec = 60
        self.drainReplyQquiet()
        if(remotePowerLevel):
            self.setValueF('TxPowerWatts', remotePowerLevel)

            # Set Remote Mode
            self.setRemoteCommand(0)
            # Issue Remote Command

            self.setValueF('TxPowerWatts', remotePowerLevel)
            repl = self.waitForSpecificReply("Alert","TxComplete", TimeoutSec)

            self.streamUpload(filename, remotePowerLevel)
            # wait for response ?

            repl = self.waitForSpecificReply("RemoteStatus",None, TimeoutSec)
            self.setLocalCommand()

            if(repl['RemoteStatus'] != "OK"):
                self.logger.error('Remote ERROR:')
                self.logger.error(repl)
                return
            # Set Local





        #check for proper response
        f = open(filename+'.download', 'wb')
        filedone =0
        TimeoutSec = 30
        while(filedone == 0):
            repl = self.waitForSpecificReply("Header",None, TimeoutSec)
            if('Timeout' in repl):
                self.logger.info ("Download Complete")
                filedone = 1
                SuperFrameDone = 1
            else:
                SuperFrameDone = 0
                TimeoutSec = 10

            while(SuperFrameDone == 0):
                reply = self.replyQ.get()
                if "Data" in reply:
                    byte_arr = reply['Data']
                    #file data in to buffer byte array
                    f.write(bytearray(byte_arr))
                elif "Alert" in reply:
                # check for CRC Error  Increment count
                    if reply['Alert'] == "CRCError":

                        byte_arr = [176] * 256
                        f.write(bytearray(byte_arr))
                elif "Info" in reply:
                    if "MODEM_Enable" in reply['Info']  :
                        SuperFrameDone = 1

            # check for Modem Enable ? is that a Info ?
            # done = 1
            # Timeout Done = 1
        f.close()


        #write byte array to disk

    def setRangeData(self, JSmessage):
        """
        Send a command to Popoto to set the response data sent to a range message

        """
        print(JSmessage)

        message = "{ \"Command\": \"setRangeData\", \"Arguments\": " + JSmessage + " }"

        print(message)
        # Verify the JSON message integrity and send along to Popoto
        try:
            testJson = json.loads(message)
            self.logger.info("Sending " + message)
            message = message + '\n'
            self.cmdsocket.sendall(message.encode())
        except json.JSONDecodeError:
            self.logger.error("Invalid JSON message: ", message)

    def payLoadModeToBitRate(self, payloadMode):
        '''
            Returns the bitrate of the selected payload mode
        '''
        try:
            rate = self.MapPayloadModesToRates[payloadMode]
        except:
            self.logger.error("Invalid payloadMode")
            rate = self.MapPayloadModesToRates[0]

        return rate

    def BitRateToPayloadMode(self, rate):
        '''
            Returns the bitrate of the selected payload mode
        '''
        try:
            PayloadMode = self.MapPayloadModesToRates.index(rate)
        except:
            self.logger.error("Invalid Rate")
            PayloadMode = 0

        return PayloadMode

    def getParametersList(self):
        """
        Gets the parameters list from the system controller.
        """
        return self.paramsList

    def getParameter(self, idx):
        """
        Gets a Popoto control element info string by element index.

        :param      idx:  The index is the reference number of the element
        :type       idx:  number
        """
        self.send('GetParameters {}'.format(idx))


    def getExclusiveAccess(self):
        '''
        Sets a token atomically on the Popoto modem.   If the token is already set,
        it returns the currently set value.
        otherwise it sets the value you request, and then returns it to you.
        this can be used to coordinate multiple clients on the popoto command socket
        '''
        self.drainReplyQquiet()
        letters = string.ascii_lowercase
        token = ''.join(random.choice(letters) for i in range(10))
        waitingCounter = 0
        accessGranted = False
        while accessGranted == False:
            self.send('SetMutexToken {}'.format(token) )
            tokenReplyReceived = False
            while(tokenReplyReceived == False):
                if waitingCounter == 5:
                    self.logger.info ("Forcing Access to Popoto")
                    self.releaseExclusiveAccess()
                    self.send('SetMutexToken {}'.format(token) )
                    waitingCounter = 0
                try:
                    reply = self.replyQ.get(True, 3)
                    if reply:
                        if "ExclusivityToken" in reply:
                            if token in reply["ExclusivityToken"]:
                                accessGranted = True
                                tokenReplyReceived = True
                            else:
                                self.logger.info("Waiting for ExclusivityToken; Process {} has it".format(reply["ExclusivityToken"]))
                                time.sleep(2)
                                tokenReplyReceived = True
                                waitingCounter += 1
                except:
                    self.logger.info ("Waiting for Exclusivity Token")
                    time.sleep(2)
                    waitingCounter += 1
                    self.send('SetMutexToken {}'.format(token))

    def releaseExclusiveAccess(self):
        self.send('SetMutexToken {}'.format('Available'))

    def getAllParameters(self):
        """
        Gets all Popoto control element info strings for all elements.
        Uses chunked GetAllParameters command for efficiency.
        Parameters are fetched over the network and kept in memory only.
        """
        verboseCache = self.verbose
        self.verbose = 0
        self.getExclusiveAccess()

        try:
            # Use chunked method
            nextIdx = 0
            all_params = []

            while nextIdx >= 0:
                self.send(f'GetAllParameters {nextIdx}')
                reply = self.replyQ.get(True, 5)

                if reply and "AllParameters" in reply:
                    chunk = reply["AllParameters"]
                    if "params" in chunk:
                        all_params.extend(chunk["params"])
                        nextIdx = chunk.get("nextIdx", -1)
                    else:
                        # Old format - single array
                        all_params = chunk
                        nextIdx = -1
                else:
                    # Fall back to legacy
                    self.logger.info("GetAllParameters not supported, using legacy method")
                    self._getAllParametersLegacy()
                    break

            if all_params:
                self.paramsList = all_params
                self.paramsList.sort(key=lambda x: x.get('Name'))
                for El in self.paramsList:
                    if El.get('Format') == 'int':
                        self.intParams[El['Name']] = El
                    else:
                        self.floatParams[El['Name']] = El
                self.logger.info(f"Loaded {len(self.paramsList)} parameters via GetAllParameters")

        except Exception as e:
            self.logger.error(f"GetAllParameters failed: {e}, trying legacy method")
            self._getAllParametersLegacy()

        self.releaseExclusiveAccess()
        self.waitForReply()
        self.verbose = verboseCache
        return

    def _getAllParametersLegacy(self):
        """
        Legacy method: iteratively fetch parameters one at a time.
        Used for backward compatibility with older firmware.
        """
        idx = 0
        seen_indices = set()

        while idx >= 0:
            # Prevent infinite loop by tracking seen indices
            if idx in seen_indices:
                self.logger.warning(f"Already visited parameter index {idx}, stopping")
                break
            seen_indices.add(idx)

            self.getParameter(idx)
            reply = self.replyQ.get(True, 3)
            if reply:
                if "Element" in reply:
                    El = reply['Element']
                    if 'nextidx' in El:
                        next_idx = int(El['nextidx'])
                        # Add to params regardless of nextidx value
                        if El.get('Format') == 'int':
                            self.intParams[El['Name']] = El
                        else:
                            self.floatParams[El['Name']] = El
                        if El.get('Channel') == 0:
                            self.paramsList.append(El)
                        # Move to next, break if done
                        if next_idx <= 0:
                            break
                        idx = next_idx
                    else:
                        self.logger.error("Element missing nextidx")
                        break
                else:
                    self.logger.info(reply)
            else:
                self.logger.error("GetParameter Timeout")
                break
# -------------------------------------------------------------------
# Popoto Internal NON Public API commands are listed below this point
# -------------------------------------------------------------------
    def RxCmdLoop(self):
        errorcount = 0
        rxString = ''
        data = b''
        self.cmdsocket.settimeout(1)
        while(self.is_running == True):
            try:
                data = self.cmdsocket.recv(1)
                if len(data) <= 0:
                    self.logger.error("Popoto appears to have been forcibly disconnected.")
                    self.cmdsocket.close()
                    disconnected = True
                    reconnect_count = 0
                    while disconnected:
                        try:
                            reconnect_count += 1
                            self.logger.error("({}) Attempting reconnect on {}:{}...".format(reconnect_count, self.ip, self.cmdport))
                            del(self.cmdsocket)
                            self.cmdsocket=socket(AF_INET, SOCK_STREAM)
                            self.cmdsocket.connect((self.ip, self.cmdport))
                            self.cmdsocket.settimeout(1)
                            self.logger.info("Reconnected to {}:{}.".format(self.ip, self.cmdport))
                            disconnected = False
                        except OSError as e:
                            if reconnect_count > 99:
                                self.logger.critical("COULD NOT CONNECT TO POPOTO_APP. PLEASE RESTART.")
                                raise e
                            time.sleep(2)
                else:
                    errorcount = 0
                    if ord(data)  != 13:
                        try:
                            rxString = rxString+str(data,'utf-8')
                        except:
                            pass

                    else:

                        idx = rxString.find("{")
                        msgType = rxString[0:idx]
                        msgType = msgType.strip()


                        jsonData = str(rxString[idx:len(rxString)])
                        try:
                            reply = json.loads(jsonData)

                            if self.verbose > 2:
                                self.logger.info("Port:" + str(self.cmdport)+ " <<<< " + str(jsonData))

                            # Intercept any registered JSON message types.
                            for key, (callback, user_arg) in self._intercept_handlers.items():
                                if key in reply:
                                    try:
                                        callback(reply, user_arg)
                                    except Exception as e:
                                        self.logger.error("Error in registered handler for '%s': %s", key, e)

                            if("RemoteCommand" in reply and self.remoteCommandHandler != None):
                                self.remoteCommandHandler.handleCommand(reply['RemoteCommand'])

                            if("RemotePshellCommand" in reply):
                                if (self.remotePshellEnabled):
                                    self.logger.info("Remote pshell command detected, executing...")
                                    self.logger.info(reply['RemotePshellCommand'])
                                    self.remotePshellCommandQueue.put(reply['RemotePshellCommand']) # To be read in default_shell
                                else:
                                    self.logger.info("Remote Command received, but remote pshell command execution is disabled.")
                                    self.logger.info("To enable this functionality, use the enableRemotePshell command.")


                            if jsonData == '{"BatteryVoltage":-0.001080}':
                                self.logger.warning("BatteryVoltage not currently measurable.")
                                self.logger.warning("(This may be due to a missing hardware component or firmware update.")
                                self.logger.warning("Contact info@popotomodem.com for more information.)")

                            self.replyQ.put(reply)
                        except Exception as e:
                            self.logger.error(e)
                            self.logger.error("Unparseable JSON message " + jsonData)

                        if(self.verbose > 1):
                            if(self.rawTerminal == False):
                                self.logger.info("\033[1m"+str(jsonData)+"\033[0m")
                            else:
                                self.logger.info(str(jsonData))
                        elif(self.verbose > 0):
                            self.logger.debug(str(jsonData))

                        rxString = ''

            except OSError:
                errorcount += 1
                time.sleep(0.01)
                continue

    def exit(self):
        print ("Stub for exit routine")

    def receive(self):
        data = self.cmdsocket.recv(256)

    def close(self):
        self.cmdsocket.close()

    def setTimeout(self, timeout):
        self.cmdsocket.settimeout(timeout)

    def getCycleCount(self):
        while self.replyQ.empty() == False:
            self.replyQ.get()

        self.getValueI('APP_CycleCount')
        reply = self.replyQ.get(True, 3)
        if reply:
            if "Application.0" in reply:
                self.dispmips(reply)

        else:
            self.logger.error("Get CycleCount Timeout")


    def dispMips(self, mips):
        v = {}
        print('Name                            |       min  |        max |     total  |      count |    average |  peak mips | avg mips')
        for module in mips:
            v = mips[module]
            name = module
            print('{:<32}|{:12}|{:12}|{:12.1e}|{:12}|{:12.1f}|{:12.1f}|{:12.1f}'.format(name,v['min'],v['max'],v['total'], v['count'], v['total']/v['count'], v['max']*160/1e6, (160/1e6)*v['total']/v['count'] ))

    def pumpAudio(self, inFilename, outFilename):
        global FRAMESIZE
        if WAVE_FILE_SUPPORTED == False:
            print ("Wave Library not installed on this platform. Try pip3 install wave to use audio pumper")
            return
        
        sock = socket(AF_INET, SOCK_STREAM)
        sock.connect((self.ip, int(self.pcmioport)) )
        sock.setblocking(True)
        
        fs, read_data = scipy_io_wavfile.read(inFilename)

        if fs == 1024000: #DELSYS 1MHz mode
            FRAMESIZE = 6400

        write_data = []
        print("Processing input wave file {} Output wave file  {}".format(inFilename, outFilename))

        rmndr = len(read_data) % FRAMESIZE

        if rmndr != 0:
            read_data = np.concatenate((read_data, np.zeros(FRAMESIZE - rmndr)))

        L = int(len(read_data)/FRAMESIZE)

        widgets = ['Test: ', Percentage(), ' ', Bar(marker='#',left='[',right=']'),' ', ETA(), ' ', FileTransferSpeed()]

        pbar = ProgressBar(widgets=widgets, maxval = L)
        pbar.start()

        for i in range(L):
            buf = read_data[i*FRAMESIZE: i*FRAMESIZE + FRAMESIZE]
            self.write_pcm(sock, buf)
            buf = self.read_pcm(sock)
            write_data.extend(buf)
            pbar.update(i)

        formatted = np.asarray(write_data).astype(np.float32)

        scipy_io_wavfile.write(outFilename, fs, formatted)
        print("\nProcessing Complete... Collecting status")
        sock.close()
        FRAMESIZE = 640 #reset
               
    def read_pcm(self, sock):
        NBYTES = FRAMESIZE * 4

        data = b''
        while len(data) < NBYTES:
            d = sock.recv(NBYTES - len(data))
            if len(d) > 0:
                data += d
            else:
                # If we receive 0 bytes, the connection may have closed
                break

        sig = struct.unpack_from(f'{FRAMESIZE}f', data)
        pcmdata = np.fromiter(sig, dtype=np.float32)
        return pcmdata
    
    def write_pcm(self, sock, buf):
        msg = struct.pack('f' * FRAMESIZE, *buf)
        totalsent = 0
        msglen = len(msg)
        while totalsent < msglen:
            sent = sock.send(msg[totalsent:])
            totalsent += sent      

    def pumpAudioDeprecated(self, inFilename, outFilename):
        if WAVE_FILE_SUPPORTED == False:
            print ("Wave Library not installed on this platform. Try pip3 install wave to use audio pumper")
            return

        pcmIn = []
        self.pcmiosocket = socket(AF_INET, SOCK_STREAM)
        self.pcmiosocket.connect((self.ip, int(self.pcmioport)) )
        self.pcmiosocket.setblocking(True)
        FrameSize = 640
        nbytes = FrameSize * 4   #sizeof(uint32_t )
        done = False
        zeroFrame = bytearray(nbytes)
        fs, read_data = scipy_io_wavfile.read(inFilename)
        print("Processing input wave file {} Output wave file  {}".format(inFilename, outFilename))
        nFramesOut=0
        write_data = []
        nframes = 0
        paddingSilenceFrames = fs  # Pad end of file with 1 second of data
        paddingSilence = 0

        if self.pcmiosocket != None:
            targetNFrames = len(read_data) + paddingSilenceFrames

            inSock = []
            outSock = []
            exceptSock = []
            inSock.append(self.pcmiosocket)
            outSock.append(self.pcmiosocket)
            exceptSock.append(self.pcmiosocket)
            widgets = ['Test: ', Percentage(), ' ', Bar(marker='#',left='[',right=']'),' ', ETA(), ' ', FileTransferSpeed()]

            pbar = ProgressBar(widgets=widgets, maxval=targetNFrames)
            pbar.start()

            while done == False:

                pbar.update(nframes)
                readable, writeable, exceptions = select.select(inSock, outSock , exceptSock)
                for sock in writeable:
                    if (nFramesOut + FrameSize) < len(read_data):

                        txout = read_data[nFramesOut:nFramesOut+FrameSize]
                        nFramesOut += FrameSize
                        sig=txout.tolist()
                        msg = struct.pack('f' * 640, *sig)
                        sock.sendall(msg)
                    else:
                        sock.sendall(zeroFrame)
                        paddingSilence += len(zeroFrame)/4

                for sock in readable:
                    data = b''
                    while len(data) < FrameSize:
                        d = sock.recv(nbytes - len(data))
                        if(len(d)>0):
                            data+=d
                    num_floats = len(data) // 4
                    sig=struct.unpack_from(f"{num_floats}f",data)
                    write_data+=sig
                    nframes= nframes + FrameSize
                    if (nframes >= targetNFrames):
                            done = True


            scipy_io_wavfile.write(outFilename, fs, np.asarray(write_data))
            print("\nProcessing Complete... Collecting status")
