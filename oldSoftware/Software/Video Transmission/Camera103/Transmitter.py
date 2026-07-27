import time
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from picamera2 import Picamera2

WIDTH = 1920
HEIGHT = 1080
FPS = 60
RECEIVER_IP = "192.168.102.100"
RECEIVER_PORT = 5000

Gst.init(None)

PIPELINE_STR = (
    f'appsrc name=source is-live=true block=true format=time '
    f'caps=video/x-raw,format=RGB,width={WIDTH},height={HEIGHT},framerate={FPS}/1 ! '
    f'videoconvert ! '
    f'x264enc tune=zerolatency speed-preset=ultrafast '
    f'bitrate=2000 byte-stream=true threads=4 key-int-max=10 ! '
    f'video/x-h264,profile=constrained-baseline ! '
    f'rtph264pay config-interval=1 pt=96 mtu=1000 ! '
    f'srtsink uri=srt://{RECEIVER_IP}:{RECEIVER_PORT}?mode=caller&latency=250'
)

pipeline = Gst.parse_launch(PIPELINE_STR)
appsrc = pipeline.get_by_name("source")

picam2 = Picamera2()
video_config = picam2.create_video_configuration(
    main={"size": (WIDTH, HEIGHT), "format": "RGB888"},
    controls={"FrameRate": FPS}
)
picam2.configure(video_config)
picam2.start()

time.sleep(1)

pipeline.set_state(Gst.State.PLAYING)

print("Streaming started... Press Ctrl+C to stop.")

frame_duration = Gst.util_uint64_scale_int(1, Gst.SECOND, FPS)
timestamp = 0

try:
    while True:
        frame = picam2.capture_array()
        data = frame.tobytes()

        buf = Gst.Buffer.new_allocate(None, len(data), None)
        buf.fill(0, data)
        buf.pts = timestamp
        buf.dts = timestamp
        buf.duration = frame_duration
        timestamp += frame_duration

        ret = appsrc.emit("push-buffer", buf)
        if ret != Gst.FlowReturn.OK:
            print("push-buffer failed:", ret)
            break

except KeyboardInterrupt:
    print("\nStopping stream...")

finally:
    appsrc.emit("end-of-stream")
    pipeline.set_state(Gst.State.NULL)
    picam2.stop()
