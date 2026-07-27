import sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

# Optimized Receiver Pipeline:
# - srtsrc: mode=listener to receive from your "caller" sender
# - rtph264depay & h264parse: Unpacks and prepares the stream
# - avdec_h264: Standard H.264 decoder
# - autovideosink: Automatically selects waylandsink or ximagesink
PIPELINE_STR = (
    "udpsrc port=5000 caps=\"application/x-rtp, payload=96\" ! "
    "rtph264depay ! h264parse ! avdec_h264 ! "
    "videoconvert ! autovideosink sync=false"
)

def start_receiver():
    pipeline = Gst.parse_launch(PIPELINE_STR)
    
    # Bus to watch for errors or EOS
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_message(bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"Error: {err}")
            loop.quit()
        elif t == Gst.MessageType.EOS:
            print("Stream ended.")
            loop.quit()

    bus.connect("message", on_message)

    # Start the pipeline
    pipeline.set_state(Gst.State.PLAYING)
    print("Receiver active. Waiting for stream on port 5000...")

    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nStopping receiver...")
    finally:
        pipeline.set_state(Gst.State.NULL)

if __name__ == "__main__":
    start_receiver()


