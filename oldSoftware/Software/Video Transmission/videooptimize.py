import cv2
import numpy as np
 
def enhance_frame(frame):
    # 1. Automatic Brightness and Contrast (Histogram Equalization on Y channel)
    yuv = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV)
    yuv[:,:,0] = cv2.equalizeHist(yuv[:,:,0])
    frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)

    # 2. Sharpening (Unsharp Masking)
    gaussian_3 = cv2.GaussianBlur(frame, (0, 0), 2.0)
    frame = cv2.addWeighted(frame, 1.5, gaussian_3, -0.5, 0)

    return frame

cap = cv2.VideoCapture(0)
 
# Set lower resolution (720p or lower)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

print("Press 's' to save the current 'ideal' frame. Press 'q' to quit.")

while True:
    ret, frame = cap.read()
    if not ret: break

    # Process frame to make it look "ideal"
    ideal_frame = enhance_frame(frame)

    # Show both for comparison
    cv2.imshow('Original (Low Res)', frame)
    cv2.imshow('Enhanced (Ideal View)', ideal_frame)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('s'):
        cv2.imwrite('ideal_capture.jpg', ideal_frame)
        print("Ideal frame saved!")

cap.release()
cv2.destroyAllWindows()