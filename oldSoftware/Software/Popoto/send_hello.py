from popoto import popoto as popoto_mod
import time

MODEM_A_IP = "10.0.0.132"   # transmitter
BASE_PORT = 17000

p = popoto_mod.popoto(MODEM_A_IP, BASE_PORT)

# Enable remote acoustic command mode
p.isRemoteCmd = True
p.remoteCommandAck = True   # request ACK (recommended)

# Sanity check
p.send("getVersion")
print(p.waitForReply(5))

# Acoustic transmit
p.send("Hello world")

# Wait for TX confirmation / ACK
print(p.waitForReply(10))

p.tearDownPopoto()
