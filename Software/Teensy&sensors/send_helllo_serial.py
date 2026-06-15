"""from popoto_serial_v3 import popoto as popoto_mod
import time

import popoto_serial as popoto_mod

p = popoto_mod.popoto("/dev/ttyUSB0", 115200, transport="serial")

p.isRemoteCmd = True
p.remoteCommandAck = True

# Sanity check
p.send("getVersion")
print("Modem Version:", p.waitForReply(5))

print("Receiver ready... Listening over SERIAL...")

try:
    while True:
        reply = p.waitForReply(30)
        if reply:
            print("Received:", reply)

except KeyboardInterrupt:
    print("Stopping receiver...")

finally:
    p.tearDownPopoto()
"""
    
import popoto_serial_v3 as popoto_mod
import time

p = popoto_mod.popoto("/dev/ttyUSB0", 115200, transport="serial")

p.isRemoteCmd = True
p.remoteCommandAck = True

# check modem
p.send("version")
print(p.waitForReply(5))

print("Sending acoustic message...")

p.send('transmit "Hello world"')
#p.send('transmitJSON { "Payload": {"Data":[72,101,108,108,111]} }')
print(p.waitForReply(10))

#print("TX Reply:", p.waitForReply(10))

#p.tearDownPopoto()



"""import popoto_serial as popoto_mod
import time

p = popoto_mod.popoto("/dev/ttyUSB0", 115200, transport="serial")

p.isRemoteCmd = True
p.remoteCommandAck = True

print("Receiver listening...")

while True:
    reply = p.waitForReply(30)
    if reply:
        print("Received:", reply)
"""