class SensorPort:
    """Owns the serial connection to the sensor board for the process lifetime."""

    def __init__(self, port: str, baud: int, timeout: float = 2.0):
        self.port, self.baud, self.timeout = port, baud, timeout
        self._ser = None

    def _ensure_open(self):
        if self._ser is not None and self._ser.is_open:
            return
        log.info("opening sensor port %s", self.port)
        self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
        time.sleep(2.0)                    # one-time board reset settle
        self._ser.reset_input_buffer()

    def read_latest(self):
        """Return the most recent complete JSON line, discarding backlog."""
        self._ensure_open()
        try:
            # Drain everything queued; keep only the last complete line.
            latest = None
            while self._ser.in_waiting > 0:
                raw = self._ser.readline()
                if raw.endswith(b"\n"):
                    latest = raw
            if latest is None:
                latest = self._ser.readline()   # nothing queued; wait for one
            if not latest:
                return None
            return json.loads(latest.decode("utf-8", errors="ignore").strip())
        except (serial.SerialException, OSError):
            log.exception("sensor port error; will reopen")
            self.close()
            return None
        except json.JSONDecodeError:
            log.warning("malformed sensor line: %r", latest[:120])
            return None

    def close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
