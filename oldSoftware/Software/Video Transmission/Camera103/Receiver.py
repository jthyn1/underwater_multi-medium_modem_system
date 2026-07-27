import sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

# The receiver pipeline string
# We use 'sync=false' to reduce latency and 'waylandsink' for Pi 5 display
# Cleaned up Receiver Pipeline
# Universal Stable Receiver
PIPELINE_STR = (
    "srtsrc uri=srt://:5000?mode=listener&latency=300 ! "
    "application/x-rtp, payload=96 ! "
    "rtph264depay ! h264parse ! avdec_h264 ! "
    "videoconvert ! waylandsink sync=false"
)

def start_receiver():
    pipeline = Gst.parse_launch(PIPELINE_STR)
    
    # Connect a bus to watch for errors or EOS (End of Stream)
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    
    def on_message(bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"Error: {err}, {debug}")
        elif t == Gst.MessageType.EOS:
            print("End of stream reached.")

    bus.connect("message", on_message)
    pipeline.set_state(Gst.State.PLAYING)
    
    print("Receiver active. Waiting for stream...")
    
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        pipeline.set_state(Gst.State.NULL)

if __name__ == "__main__":
    start_receiver()

