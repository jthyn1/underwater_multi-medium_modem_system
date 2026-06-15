from picamera2 import Picamera2, Preview

picam2 = Picamera2()
config = picam2.create_preview_configuration()
picam2.configure(config)

picam2.start_preview(Preview.QTGL)
picam2.start()

input("Press Enter to stop the camera...")

picam2.stop()