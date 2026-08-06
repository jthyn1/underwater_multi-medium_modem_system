# live-feedback
SRT VIDEO STREAMING WITH RASPBERRY PI

This repository contains two Python programs that implement a
low-latency video streaming system using GStreamer and a Raspberry Pi
camera.

Files: - transmitter.py → Captures video and sends it over the network
- receive.py → Listens for the stream and displays it

  ---------------
  FILE OVERVIEW
  ---------------

transmitter.py — Video Sender

This script captures live video from the Raspberry Pi camera using
Picamera2 and streams it over the network using SRT.

Key Responsibilities: - Initializes the camera at 320x240 resolution and
8 FPS - Converts frames into a format usable by GStreamer - Encodes
video using H.264 (x264 encoder) - Sends the stream using SRT to a
receiver IP

Important Settings: - RECEIVER_IP → IP address of the receiver -
RECEIVER_PORT → Port used for streaming (default: 5000)

Simple Explanation: Captures camera frames → encodes them → streams them
over the network

------------------------------------------------------------------------

receive.py — Video Receiver

This script listens for an incoming SRT stream and displays it using
GStreamer.

Key Responsibilities: - Opens an SRT listener on port 5000 - Receives
RTP packets carrying H.264 video - Decodes the video stream - Displays
video using Wayland (optimized for Raspberry Pi 5) - Handles errors and
stream termination

Simple Explanation: Waits for incoming stream → decodes video → displays
it

  --------------
  HOW IT WORKS
  --------------

1.  Run receive.py on the receiving device
2.  Run transmitter.py on the sending device
3.  The transmitter connects to the receiver using SRT
4.  Video is streamed in real time with low latency

  --------------
  REQUIREMENTS
  --------------

-   Python 3
-   GStreamer with RTP, x264, and SRT plugins
-   Picamera2
-   Raspberry Pi (recommended)

  ---------------
  EXAMPLE USAGE
  ---------------

On Receiver (Device A): python3 receive.py

On Transmitter (Device B): python3 transmitter.py

  -------
  NOTES
  -------

-   Designed for low bandwidth and low latency streaming
-   Uses SRT (Secure Reliable Transport) for reliability
-   Resolution and FPS are kept low for performance
-   Can be scaled to higher resolutions with better hardware
