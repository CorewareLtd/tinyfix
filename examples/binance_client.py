#!/usr/bin/python
#
# YOU NEED TO RUN STUNNEL BEFORE RUNNING THIS ONE :
#
#   sudo apt install stunnel4
#   sudo stunnel binance_fix_stunnel.conf
#
import time
import signal
from tinyfix import *

#python -m pip install cryptography
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.backends import default_backend

API_KEY = "YOUR_API_KEY"
KEY_FILE_PEM = "ed25519_private.pem"

IS_EXITING = False
LOGOUT_SENT = False

#https://github.com/binance/binance-spot-api-docs/blob/master/testnet/fix-api.md#how-to-sign-logon-a-request
def get_binance_logon_signature(private_key_pem_file, message_type, sender_compid, target_compid, seqno, sending_time):
    SOH = "\x01"

    def _val(x):
        s = str(x)
        return s.split("=", 1)[1] if "=" in s else s

    payload = SOH.join([
        _val(message_type),
        _val(sender_compid),
        _val(target_compid),
        _val(seqno),
        _val(sending_time),
    ])

    with open(private_key_pem_file, "rb") as f:
        key = serialization.load_pem_private_key(
            f.read(),
            password=None,
            backend=default_backend()
        )

    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"Loaded key is {type(key)}, expected Ed25519PrivateKey")

    sig = key.sign(payload.encode("ascii"))
    return base64.b64encode(sig).decode("ascii")

class MyClientHandler(FixClientHandler):
    def handle(self):
        global LOGOUT_SENT
        self.query_id = 0
        while True:
            if IS_EXITING is True:
                if self.fix_session.state == SessionState.LOGGED_ON:
                    self.send_logout()
                LOGOUT_SENT = True
                return

            if self.fix_session.state == SessionState.DISCONNECTED:
                self.connect(5)

                if self.fix_session.state == SessionState.PENDING_LOGON:
                    self.send_logon()

            else:
                self.send_heartbeat_if_necessary()

                current_msg = self.get_next_fix_message()

                if current_msg is None:
                    continue

                print("Received: " + current_msg.to_string() + "\n")
                msg_type = current_msg.get_tag_value(35)

                if msg_type == "A":
                    self.fix_session.state = SessionState.LOGGED_ON
                    print("Logon accepted.\n")
                    self.send_query_limits()

                if msg_type == "XLR":
                    xlr_group_count = int(current_msg.get_repeating_tag_value(25003, 0))

                    for i in range(xlr_group_count):
                        t25004 = current_msg.get_repeating_tag_value(25004, i)
                        t25005 = current_msg.get_repeating_tag_value(25005, i)
                        t25006 = current_msg.get_repeating_tag_value(25006, i)
                        t25007 = current_msg.get_repeating_tag_value(25007, i)

                        print("XLR group " + str(i+1) + ": " + str(t25004) + " " + str(t25005) + " " + str(t25006) + " " + str(t25007))

    def on_disconnection(self):
        print("Connection lost\n")

    def send_logon(self):
        logon = FixMessage()
        logon.set_msg_type("A")
        logon.set_tag(25000, "60000")
        logon.set_tag(141, "Y")
        logon.set_tag(98, "0")
        logon.set_tag(108, str(self.fix_session.heartbeat_interval))
        logon.set_tag(553, API_KEY)
        logon.set_tag(25035, "1")

        binance_signature = get_binance_logon_signature(KEY_FILE_PEM, "A", self.fix_session.comp_id, self.fix_session.target_comp_id, 1, logon.get_sending_time(self.fix_session))

        if len(binance_signature)>0:
            logon.set_tag(95, len(binance_signature))
            logon.set_tag(96, binance_signature)
        else:
            print("get_binance_logon_signature failed")
            return

        self.send(logon)
        print("Sent logon message \n")

    def send_query_limits(self):
        query = FixMessage()
        query.set_msg_type("XLQ")

        self.query_id = self.query_id + 1
        query.set_tag(6136, self.query_id)

        self.send(query)
        print("Sent query limits message : " + query.to_string() + "\n")

    def send_logout(self):
        logout = FixMessage()
        logout.set_msg_type("5")
        self.send(logout)
        print("Sent logout : " + logout.to_string() + "\n")

    def send_heartbeat_if_necessary(self):
        if self.fix_session.last_sent_time is not None:
            if time.time() - self.fix_session.last_sent_time >= self.fix_session.heartbeat_interval:
                hb = FixMessage()
                hb.set_msg_type("0")

                self.send(hb)
                print("Sent Heartbeat.\n")

def signal_handler(signum, frame):
        global IS_EXITING
        print('You pressed Ctrl+C!')
        IS_EXITING = True

def main():
    try:
        signal.signal(signal.SIGINT, signal_handler)

        # FIX SESSION
        fix_session = FixSession()
        fix_session.begin_string = "FIX.4.4"
        fix_session.comp_id = "TINYFIX"
        fix_session.target_comp_id = "SPOT"
        #fix_session.endpoint_address = "fix-oe.testnet.binance.vision"
        #fix_session.port = 9000
        # Stunnel config
        fix_session.endpoint_address = "127.0.0.1"
        fix_session.port = 19000
        fix_session.heartbeat_interval=5
        fix_session.timestamp_subsecond_precision = TimestampSubsecondPrecision.MILLI
        #fix_session.bind_address="YOUR_NIC_ADDRESS"

        # FIX CLIENT
        client = FixClient(fix_session, MyClientHandler)
        client.start()

        while True:
            if IS_EXITING is True:
                break
            time.sleep(1)

        while True:
            if LOGOUT_SENT is True:
                break
        print('Exiting')

    except ValueError as err:
        print(err.args)

#Entry point
if __name__ == "__main__":
    main()
