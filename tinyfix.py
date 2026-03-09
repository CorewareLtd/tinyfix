#!/usr/bin/python
# VERSION 1.0.0
'''
MIT License

Copyright (c) 2026 Coreware Limited

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''
from datetime import datetime, timezone
import socketserver
import socket
from enum import Enum
import time
import os
import threading

#----------------------------------------------------------------------------------
# FIX CONSTANTS
class FixConstants:
    # GENERAL
    EQUALS = '='
    SOH = chr(1)
    PIPE='|'

#----------------------------------------------------------------------------------
# FIX UTILS
class FixUtils:
    @staticmethod
    def fix_to_readable(fix_str):
        return fix_str.replace(FixConstants.SOH, FixConstants.PIPE)

    @staticmethod
    def readable_to_fix(readable_str):
        return readable_str.replace(FixConstants.PIPE, FixConstants.SOH)

#----------------------------
# SESSION STATE
class SessionState(Enum):
    NONE = 0
    DISCONNECTED = 1
    PENDING_CONNECTION = 2
    PENDING_LOGON = 3
    PENDING_LOGOUT = 4
    LOGGED_ON = 5
    LOGGED_OUT = 6
    LOGON_REJECTED = 7
    IN_RETRANSMISSION_INITIATED_BY_SELF = 8
    IN_RETRANSMISSION_INITIATED_BY_PEER = 9

#----------------------------------------------------------------------------------
# TIMESTAMPS
class TimestampSubsecondPrecision(Enum):
    NANO = 1
    MICRO = 2
    MILLI = 3
    NONE = 4

class UTCTimestamp:
    @staticmethod
    def get_current_date_time_string(subsecond_precision):
        if subsecond_precision == TimestampSubsecondPrecision.NANO:
            return UTCTimestamp.get_current_date_time_nano()
        if subsecond_precision == TimestampSubsecondPrecision.MICRO:
            return UTCTimestamp.get_current_date_time_micro()
        if subsecond_precision == TimestampSubsecondPrecision.MILLI:
            return UTCTimestamp.get_current_date_time_milli()

        return UTCTimestamp.get_current_date_time_none()

    @staticmethod
    def get_current_date_time_nano():
        now = datetime.now(timezone.utc)
        date_part = now.strftime('%Y%m%d-%H:%M:%S')
        nanosec = f"{now.microsecond * 1000:09d}"
        return f"{date_part}.{nanosec}"

    @staticmethod
    def get_current_date_time_micro():
        now = datetime.now(timezone.utc)
        date_part = now.strftime('%Y%m%d-%H:%M:%S')
        microsec = f"{now.microsecond:06d}"
        return f"{date_part}.{microsec}"

    @staticmethod
    def get_current_date_time_milli():
        now = datetime.now(timezone.utc)
        date_part = now.strftime('%Y%m%d-%H:%M:%S')
        millisec = f"{now.microsecond // 1000:03d}"
        return f"{date_part}.{millisec}"

    @staticmethod
    def get_current_date_time_none():
        now = datetime.now(timezone.utc)
        return now.strftime('%Y%m%d-%H:%M:%S')

#----------------------------------------------------------------------------------
# FIX SESSION
class FixSession:
    def __init__(self):
        self.begin_string = ""
        self.comp_id = ""
        self.target_comp_id = ""
        self.outgoing_seq_no = 0
        self.incoming_seq_no = 0
        self.endpoint_address = ""
        self.bind_address = ""
        self.port = 0
        self.state = SessionState.NONE
        self.heartbeat_interval=30
        self.last_sent_time = None
        self.last_received_time = None
        self.timestamp_subsecond_precision = TimestampSubsecondPrecision.NANO
        self.sequence_store_file_path = ""
        self.receive_size = 128

    def get_current_datetime_string(self):
        return UTCTimestamp.get_current_date_time_string(self.timestamp_subsecond_precision)

    def increment_outgoing_seq_no(self):
        self.outgoing_seq_no += 1
        if len(self.sequence_store_file_path)>0:
            self.save_sequence_nos_to_store_file()

    def increment_incoming_seq_no(self):
        self.incoming_seq_no += 1
        if len(self.sequence_store_file_path)>0:
            self.save_sequence_nos_to_store_file()

    def update_last_sent_time(self):
        self.last_sent_time = time.time()

    def update_last_received_time(self):
        self.last_received_time = time.time()

    def save_sequence_nos_to_store_file(self):
        tmp_path = self.sequence_store_file_path + ".tmp"
        with open(tmp_path, "w") as textFile:
            textFile.write(str(self.incoming_seq_no) + "," + str(self.outgoing_seq_no))
        os.replace(tmp_path, self.sequence_store_file_path)

    def set_sequence_store_file(self, store_file_path):
        self.sequence_store_file_path = store_file_path
        self.load_sequence_nos_from_store_file()

    def load_sequence_nos_from_store_file(self):
        incoming = 0
        outgoing = 0

        try:
            if os.path.exists(self.sequence_store_file_path):
                with open(self.sequence_store_file_path, "r") as fileContent:
                    line = fileContent.readline()
                    str_incoming = line.split(',')[0]
                    str_outgoing = line.split(',')[1]

                    if str_incoming.isdigit() is True:
                        incoming = int(str_incoming)

                    if str_outgoing.isdigit() is True:
                        outgoing = int(str_outgoing)
        except:
            print("Warning : Error during opening sequence number file , sequence numbers set to 0")

        self.incoming_seq_no = incoming
        self.outgoing_seq_no = outgoing

#----------------------------------------------------------------------------------
# FIX MESSAGE
class FixTagValuePair:
    def __init__(self):
        self.tag=0
        self.value=""
        self.is_dirty=True

class FixMessage:
    def __init__(self):
        # Using array to be able to support repeating groups
        self.msg_type = ""
        self.tag_values = []
        self.tag_values_index = 0

        for i in range(512):
            pair = FixTagValuePair()
            pair.is_dirty = True
            self.tag_values.append(pair)

        self.encoded = ""
        self.decoded = ""
        self.cached_sending_time = ""

    def set_msg_type(self, value_string):
        self.msg_type = value_string

    def set_tag(self, tag_int, value_string):
        if len(self.tag_values) == self.tag_values_index:
            pair = FixTagValuePair()
            pair.is_dirty = True
            self.tag_values.append(pair)

        self.tag_values[self.tag_values_index].tag = tag_int
        self.tag_values[self.tag_values_index].value = value_string
        self.tag_values[self.tag_values_index].is_dirty = False
        self.tag_values_index += 1

    def get_tag_value(self, tag):
        for pair in self.tag_values:
            if pair.is_dirty == False:
                if tag == pair.tag:
                    return pair.value
        return None

    def get_repeating_tag_value(self, tag, index):
        observed_count = 0
        for pair in self.tag_values:
            if pair.is_dirty == False:
                if tag == pair.tag:
                    if observed_count == index:
                        return pair.value
                    observed_count += 1
        return None

    def has_tag(self, tag):
        for pair in self.tag_values:
            if pair.is_dirty == False:
                if tag == pair.tag:
                    return True
        return False

    def get_encoded(self):
        return self.encoded

    def calculate_body_length(self, fix_session):
        length = 0

        length += 4 + len(self.msg_type)                                    #4-> 35 & = & delimiter
        length += 4 + len(fix_session.comp_id)                              #4-> 49 & = & delimiter
        length += 4 + len(fix_session.target_comp_id)                       #4-> 56 & = & delimiter
        length += 4 + len(str(fix_session.outgoing_seq_no))                 #4-> 34 & = & delimiter
        length += 4 + len(str(fix_session.get_current_datetime_string()))   #4-> 52 & = & delimiter

        for pair in self.tag_values:
            if pair.is_dirty == False:
                length += 2 + len(str(pair.tag)) + len(str(pair.value)) #2-> = & delimiter

        return length

    def get_checksum_string(self):
        checksum_data = self.encoded.encode('ascii')
        checksum = sum(checksum_data) % 256
        return f"{checksum:03}"  # always 3-digit format with leading zeros

    def get_sending_time(self, fix_session):
        self.cached_sending_time = fix_session.get_current_datetime_string()
        return self.cached_sending_time

    def encode(self, fix_session):
        body_length = self.calculate_body_length(fix_session)

        self.encoded = ""

        self.encoded += "8=" + fix_session.begin_string + FixConstants.SOH
        self.encoded += "9=" + str(body_length) + FixConstants.SOH

        self.encoded += "35=" + self.msg_type+ FixConstants.SOH

        self.encoded += "34=" + str(fix_session.outgoing_seq_no) + FixConstants.SOH
        self.encoded += "49=" + fix_session.comp_id + FixConstants.SOH
        self.encoded += "56=" + fix_session.target_comp_id + FixConstants.SOH

        if len(self.cached_sending_time)==0:
            self.cached_sending_time = fix_session.get_current_datetime_string()

        self.encoded += "52=" + self.cached_sending_time + FixConstants.SOH

        for pair in self.tag_values:
            if pair.is_dirty == False:
                self.encoded +=  str(pair.tag) + FixConstants.EQUALS + str(pair.value) + FixConstants.SOH
                pair.is_dirty = True

        self.encoded += "10=" + self.get_checksum_string() + FixConstants.SOH

        self.tag_values_index = 0

    def decode_from(self, decode_bytes):
        # Store decoded full message as string (for to_string())
        try:
            self.decoded = decode_bytes.decode("ascii")
        except UnicodeDecodeError:
            self.decoded = decode_bytes.decode("latin-1")

        tag_value_pairs = decode_bytes.split(b"\x01")

        for tv in tag_value_pairs:
            if b"=" in tv:
                tag_b, val_b = tv.split(b"=", 1)

                if tag_b.isdigit():
                    current_tag = int(tag_b)

                    try:
                        current_value = val_b.decode("ascii")
                    except UnicodeDecodeError:
                        current_value = val_b.decode("latin-1")

                    self.set_tag(int(current_tag), str(current_value))

    def to_string(self):
        ret = ""

        if len(self.encoded) > 0:
            ret = self.encoded
        elif len(self.decoded)>0:
            ret = self.decoded

        if len(ret)>0:
            ret = FixUtils.fix_to_readable(ret)
        return ret

#----------------------------------------------------------------------------------
# FIX PARSER
class FixParser:
    def __init__(self):
        self.buffer = b""

    def append(self, append_bytes):
        if append_bytes is not None:
            if len(append_bytes) > 0:
                self.buffer += append_bytes

    def get_next_fix_message(self):
        # Look for SOH + "10=" which marks the end of a FIX message
        checksum_index = self.buffer.find(b"\x0110=")
        if checksum_index == -1:
            return None

        checksum_index = checksum_index + 1 # +1 due to SOH

        # Find the SOH after the checksum to complete the message
        soh_after_checksum = self.buffer.find(b"\x01", checksum_index)
        if soh_after_checksum == -1:
            return None

        end = soh_after_checksum + 1
        message = self.buffer[:end]
        self.buffer = self.buffer[end:] # Remove the parsed message from buffer

        fix_msg = FixMessage()
        fix_msg.decode_from(message)
        return fix_msg

#----------------------------------------------------------------------------------
# FIX SERVER
class FixServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
        allow_reuse_address = True

        def __init__(self, server_address, RequestHandlerClass):
            socketserver.TCPServer.__init__(self, server_address, RequestHandlerClass)
            self.fix_sessions = []
            self._thread = None

        def start(self):
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self.serve_forever, daemon=True)
            self._thread.start()

        def stop(self):
            try:
                self.shutdown()
            finally:
                self.server_close()

        def add_client_fix_session(self, fix_session):
            self.fix_sessions.append(fix_session)

        def supports_client_session(self, fix_message):
            for session in self.fix_sessions:
                if fix_message.get_tag_value(8) == session.begin_string and fix_message.get_tag_value(49) == session.target_comp_id and fix_message.get_tag_value(56) == session.comp_id:
                    return True
            return False

        def get_fix_session(self, peer_comp_id):
            for session in self.fix_sessions:
                if peer_comp_id == session.target_comp_id:
                    return session

class FixServerHandler(socketserver.BaseRequestHandler):
    def setup(self):
        self.fix_session = None
        self.fix_parser = FixParser()
        self.request.settimeout(1.0)

    def send(self, fix_message):
        if self.fix_session is not None:
            if self.fix_session.state is SessionState.DISCONNECTED:
                return
        try:
            self.fix_session.increment_outgoing_seq_no()
            fix_message.encode(self.fix_session)
            self.request.sendall(fix_message.get_encoded().encode())
            self.fix_session.update_last_sent_time()
        except socket.error as e:
            if self.fix_session is not None:
                self.fix_session.state = SessionState.DISCONNECTED
            self.on_disconnection()

    def initialise_session_from_logon_message(self, logon_message):
        peer_comp_id = logon_message.get_tag_value(49)
        self.fix_session = self.server.get_fix_session(peer_comp_id)

    def receive(self):
        recv_size = 128

        if self.fix_session is not None:
            if self.fix_session.state is SessionState.DISCONNECTED:
                return
            recv_size = self.fix_session.receive_size
        data = None

        try:
            data = (self.request.recv(recv_size))
        except socket.timeout:
            data = None
        except socket.error as e:
            if self.fix_session is not None:
                self.fix_session.state = SessionState.DISCONNECTED
            self.on_disconnection()
            return

        if data is not None:
            if len(data)>0:
                self.fix_parser.append(data)
                if self.fix_session is not None:
                    self.fix_session.update_last_received_time()

    def get_next_fix_message(self):
        self.receive()
        ret = self.fix_parser.get_next_fix_message()
        if self.fix_session is not None:
            if ret is not None:
                self.fix_session.increment_incoming_seq_no()
        return ret

    def on_disconnection(self):
        """Override this method in subclasses"""
        pass

#----------------------------------------------------------------------------------
# FIX CLIENT
class FixClient:
    def __init__(self, fix_session, handler_class):
        self.fix_session = fix_session
        self.handler_class = handler_class
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            print("FixClient is already running")
            return

        self.thread = threading.Thread(target=self.run_handler, daemon=True)
        self.thread.start()

    def run_handler(self):
        self.fix_session.state = SessionState.DISCONNECTED
        self.handler = self.handler_class(self.fix_session)
        self.handler.handle()

    def stop(self):
        try:
            self.handler.close()
        except Exception:
            pass
        self.fix_session.state = SessionState.DISCONNECTED

        if self.thread is not None:
            self.thread.join()

class FixClientHandler:
    def __init__(self, fix_session):
        self.sock = None
        self.fix_session = fix_session
        self.fix_parser = FixParser()

    def connect(self, timeout_seconds=5):
        self.fix_session.state = SessionState.PENDING_CONNECTION

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout_seconds)

        if len(self.fix_session.bind_address)>0:
            if self.fix_session.bind_address != self.fix_session.endpoint_address and self.fix_session.endpoint_address != "127.0.0.1" and self.fix_session.endpoint_address.lower() != "localhost":
                self.sock.bind((self.fix_session.bind_address, 0))

        try:
            self.sock.connect((self.fix_session.endpoint_address, self.fix_session.port))
            print(f"Connected to {self.fix_session.endpoint_address}:{self.fix_session.port}\n")
        except socket.timeout:
            self.fix_session.state = SessionState.DISCONNECTED
            print("Connection timed out\n")
            return
        except socket.error as e:
            self.fix_session.state = SessionState.DISCONNECTED
            print(f"Connection failed: {e}\n")
            return

        self.fix_session.state = SessionState.PENDING_LOGON

    def send(self, fix_message):
        if self.fix_session.state is not SessionState.DISCONNECTED:
            try:
                self.fix_session.increment_outgoing_seq_no()
                fix_message.encode(self.fix_session)
                self.sock.sendall(fix_message.get_encoded().encode())
                self.fix_session.update_last_sent_time()
            except socket.error as e:
                if self.fix_session.state != SessionState.DISCONNECTED and self.fix_session.state != SessionState.NONE and self.fix_session.state != SessionState.PENDING_CONNECTION:
                    self.on_disconnection()
                self.fix_session.state = SessionState.DISCONNECTED

    def receive(self):
        if self.fix_session.state is not SessionState.DISCONNECTED:
            data = None

            try:
                data = self.sock.recv(self.fix_session.receive_size)
            except socket.timeout:
                data = None
            except socket.error as e:
                if self.fix_session.state != SessionState.DISCONNECTED and self.fix_session.state != SessionState.NONE and self.fix_session.state != SessionState.PENDING_CONNECTION:
                    self.on_disconnection()
                self.fix_session.state = SessionState.DISCONNECTED
                return

            if data is not None:
                if len(data)>0:
                    self.fix_session.update_last_received_time()
                    self.fix_parser.append(data)

    def get_next_fix_message(self):
        self.receive()
        ret = self.fix_parser.get_next_fix_message()
        if ret is not None:
            self.fix_session.increment_incoming_seq_no()
        return ret

    def handle(self):
        """Override this method in subclasses"""
        pass

    def on_disconnection(self):
        """Override this method in subclasses"""
        pass

    def close(self):
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            self.sock.close()
        self.fix_session.state = SessionState.DISCONNECTED