import socket
import threading
import time
from datetime import datetime


class UDPManager:
    """
    Handles UDP marker broadcast and listening for all experiments.

    Usage:
        udp = UDPManager(host, send_port, listen_port)

        # at experiment start
        udp.start_listen(log_path, get_t0=lambda: recorder.t0)
        udp.send(0, 1, log_path, get_t0=lambda: recorder.t0)

        # at experiment stop
        udp.stop_listen()

        # on app close
        udp.close()
    """

    def __init__(self, host: str, send_port: int, listen_port: int):
        self.host        = host
        self.send_port   = send_port
        self.listen_port = listen_port

        self._send_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock  = None
        self._recv_thread = None
        self._recv_stop  = threading.Event()
        self._last_send_t: dict = {}   # key "prev,new" → t_rel at send time

    # Send
    
    def send(self, prev: int, new: int, log_path: str, get_t0):
        """
        Broadcast a marker pair (prev, new) and log it to file.

        Args:
            prev     : previous marker value
            new      : new marker value
            log_path : path to the UDP log .txt file
            get_t0   : callable → float, returns recording start timestamp
        """
        key = f"{prev},{new}"
        try:
            self._send_sock.sendto(
                f"{prev},{new}".encode(), (self.host, self.send_port))
        except Exception:
            pass
        try:
            t_rel = time.perf_counter() - get_t0()
            t_abs = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            self._last_send_t[key] = t_rel
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"SEND\t{t_rel:.6f}\t{t_abs}\t{prev}\t{new}\t\n")
        except Exception:
            pass

    # Listen

    def start_listen(self, log_path: str, get_t0):
        """
        Start UDP receive thread. Stops any previous listener first.

        Args:
            log_path : path to the UDP log .txt file
            get_t0   : callable → float, returns recording start timestamp
        """
        self.stop_listen()
        self._last_send_t = {}
        self._recv_stop.clear()
        try:
            self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._recv_sock.bind(("", self.listen_port))
            self._recv_sock.settimeout(1.0)
            print(f"[UDP] Listening on port {self.listen_port}")
            self._recv_thread = threading.Thread(
                target=self._recv_loop,
                args=(log_path, get_t0),
                daemon=True)
            self._recv_thread.start()
        except Exception as e:
            print(f"[UDP] Failed to start listener: {e}")

    def stop_listen(self):
        """Stop receive thread and close receive socket."""
        self._recv_stop.set()
        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=2.0)
        self._recv_thread = None
        if self._recv_sock:
            try:
                self._recv_sock.close()
            except Exception:
                pass
            self._recv_sock = None

    def close(self):
        """Stop listener and close send socket. Call on app exit."""
        self.stop_listen()
        try:
            self._send_sock.close()
        except Exception:
            pass

    # Internal

    def _recv_loop(self, log_path: str, get_t0):
        print(f"[UDP] recv thread started, waiting on port {self.listen_port}")
        while not self._recv_stop.is_set():
            try:
                data, addr = self._recv_sock.recvfrom(256)
                print(f"[UDP] received from {addr}: {data!r}")
                t_rel = time.perf_counter() - get_t0()
                t_abs = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
                msg   = data.decode("utf-8", errors="ignore").strip()
                rtt   = ""
                if msg in self._last_send_t:
                    rtt = f"{t_rel - self._last_send_t[msg]:.6f}"
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"RECV\t{t_rel:.6f}\t{t_abs}\t{msg}\t\t{rtt}\n")
                except Exception:
                    pass
            except socket.timeout:
                continue
            except OSError:
                if self._recv_stop.is_set():
                    break
                print(f"[UDP] recv socket closed unexpectedly")
                break
            except Exception as e:
                print(f"[UDP] recv loop error: {e}")
                break
        print("[UDP] recv thread exited")
