import socket
import select
import signal
import time
import sys
import queue

def set_baud(baud, bits=7):
    global BAUD_NS
    BAUD_NS = int(1e9/(baud/(bits + 2)))

set_baud(300)

TIGHT_LOOP_LEN = 5
TIGHT_LOOP_COUNT = 5
SLEEP_FOR_IO = 10

########################################
# select various input sources
########################################

select_fd = {}

def select_fd_on(name, fd, callback):
    select_fd[name] = (fd, callback)

def select_fd_off(name):
    del select_fd[name]

def sleep_for_input(timeout):
    # warn: this will not return when ^C is pressed and caught
    rlist, _, _ = select.select(list(fd for fd, callback in select_fd.values()), [], [], timeout)
    for x in rlist:
        for name, fd_callback in select_fd.items():
            fd, callback = fd_callback
            if x == fd:
                callback(name, fd)
                break
        else:
            print("sleep_for_input error")

def io_loop():
    while True:
        sleep_for_input(SLEEP_FOR_IO)

########################################
# devices
########################################

class StatusDevice:
    """The TTY reports its TX and RX status through this device"""
    def __init__(self):
        self.name = "TTY Status"
        self.tx_rdy = True
        self.rx_rdy = False

        self.monitored_devices = []
        self.halt = False

        self.prev_instr_count = 0
        self.in_tight_loop_count = 0

    def add_monitored_device(self, device):
        self.monitored_devices.append(device)

    def get_device_input(self, cpu):
        if self.halt:
            return -1

        # detect if we're in a tight loop
        elapsed_instr_count = cpu.instr_count - self.prev_instr_count
        self.prev_instr_count = cpu.instr_count
        in_tight_loop = False
        if elapsed_instr_count <= TIGHT_LOOP_LEN:
            if self.in_tight_loop_count < TIGHT_LOOP_COUNT:
                self.in_tight_loop_count += 1
            else:
                in_tight_loop = True
        else:
            self.in_tight_loop_count = 0

        # notify devices they are being monitored, let them take action to exit the tight loop
        for device in self.monitored_devices:
            if device.status_checked(cpu, in_tight_loop):
                in_tight_loop = False

        # if this really is a tight loop, chill
        if in_tight_loop:
            if cpu.debug_fh:
                print("SLEEP STAT %04x %d"%(cpu.pc-2, elapsed_instr_count), file=cpu.debug_fh)
            sleep_for_input(SLEEP_FOR_IO)
        else:
            sleep_for_input(0.001)

        # return the status
        return self.tx_rdy * 0x01 | self.rx_rdy * 0x02

class ScriptedInputDevice:
    def __init__(self, name, status_device, cpu):
        self.name = name
        self.status_device = status_device
        status_device.add_monitored_device(self)
        self.bad_time_addr = cpu.sym_to_mem.get('TSTCC',
            cpu.sym_to_mem.get('TSTCH', 0))

        self.stack = None

    def load_file(self, text_file_name):
        fh = open(text_file_name)
        string = "NEW\rTAPE\r" + "\r".join((line.strip() for line in fh)) + "\rKEY\rRUN\r"
        self.stack = [ord(c) for c in string]
        self.stack.reverse()
        fh.close()

    def status_checked(self, cpu, in_tight_loop):
        bad_time = self.bad_time_addr == cpu.pc - 2
        if bad_time:
            self.status_device.rx_rdy = False
        elif self.stack:
            self.status_device.rx_rdy = True
        return True

    def get_device_input(self, cpu):
        if self.status_device.halt:
            return -1

        self.status_device.rx_rdy = False
        if self.stack:
            return self.stack.pop()
        print("ERROR1")
        sys.exit(1)

    def done(self):
        if self.stack:
            print("Remaining unread chars: %d"%len(self.stack))

class ConsoleInputDevice:
    def __init__(self, name, status_device):
        global SLEEP_FOR_IO

        self.name = name
        self.status_device = status_device

        status_device.add_monitored_device(self)
        self.stack = None

        SLEEP_FOR_IO = 0.2
        signal.signal(signal.SIGINT, self.sigint_handler)
        self.count_ctrl_c = 0
        self.in_time = 0

        select_fd_on("stdin", sys.stdin, self.keyboard)

    def keyboard(self, name, fd):
        string = sys.stdin.readline().upper()
        # pressing ^D will produce empty string
        if not string:
            self.status_device.halt = True
        else:
            string = string.rstrip()
            self.stack = [ord(c) for c in string]
            self.stack.append(ord('\r'))
            self.stack.reverse()
            self.status_device.rx_rdy = True

    def status_checked(self, cpu, in_tight_loop):
        # delay for BAUD rate before allowing another key
        if time.time_ns() - self.in_time < BAUD_NS:
            return bool(self.stack)

        # if there is another key, signal RX ready
        if self.stack or self.count_ctrl_c:
            if not self.status_device.rx_rdy:
                self.status_device.rx_rdy = True

        # if keys buffered, don't allow tight loop
        return bool(self.stack)

    def sigint_handler(self, sig, frame):
        if sig == 2:
            self.count_ctrl_c += 1
            if self.count_ctrl_c > 3:
                print("EXIT due to CTRL-C")
                sys.exit(1)
            self.status_device.rx_rdy = True
        else:
            print('UNEXPECTED SIG %d'%sig)

    def get_device_input(self, cpu):
        if self.status_device.halt:
            return -1

        # delay for BAUD rate before allowing another key
        if time.time_ns() - self.in_time < BAUD_NS:
            return 0

        key = 0
        if self.count_ctrl_c:
            self.count_ctrl_c = 0
            key = 3
        elif self.stack:
            key = self.stack.pop()
        if not key:
            print("EXIT due to no key")
            sys.exit(0)

        # mark keyboard not ready, record when this key was returned
        self.status_device.rx_rdy = False
        self.in_time = time.time_ns()

        # return one key
        return key

    def done(self):
        pass

class ConsoleOutputDevice:
    def __init__(self, name, status_device, ignore_baud=False):
        self.name = name
        self.status_device = status_device
        self.ignore_baud = ignore_baud
        status_device.add_monitored_device(self)

        self.line = []
        self.out_time = 0

    def status_checked(self, cpu, in_tight_loop):
        now_time = time.time_ns()

        # if the output device is ready, signal it
        out_was_not_ready = not self.status_device.tx_rdy
        if now_time - self.out_time >= BAUD_NS:
            self.status_device.tx_rdy = True

        return out_was_not_ready

    def put_output(self, c):
        # mark output not ready, to simulate the BAUD rate
        self.out_time = time.time_ns()
        if not self.ignore_baud:
            self.status_device.tx_rdy = False

        if 0 < c < 127:
            print(chr(c), end='', flush=True)
        if 32 <= c < 127:
            self.line.append(chr(c))
        if c == 0x0d:
            line = "".join(self.line)
            if line == "BYE BYE":
                self.status_device.halt = True
            self.line = []

    def done(self):
        pass

class SocketTTYDevice:
    def __init__(self, name, status_device, port):
        self.name = name
        self.status_device = status_device
        status_device.add_monitored_device(self)

        self.src_socket = None
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(('localhost', port))
        self.server_socket.listen(0x40)
        select_fd_on("svr_socket", self.server_socket, self.accept_socket)

        self.queue = queue.Queue()
        self.in_time = 0
        self.out_time = 0

        self.prev_instr_count = 0
        self.in_tight_loop_count = 0

    def done(self):
        pass

    ########################################
    # socket I/O
    ########################################

    def read_socket(self, name, fd):
        buffer = self.src_socket.recv(4096)
        if len(buffer) == 0:
            self.src_socket.close()
            self.src_socket = None
            select_fd_off("socket")
        for c in buffer:
            if 0 < c < 0x80:
                # uppercase
                if ord('a') <= c <= ord('z'):
                    c -= 0x20

                self.queue.put(c)

    def accept_socket(self, name, fd):
        if self.src_socket:
            src_socket, src_address = fd.accept()
            src_socket.send(b"connection already exists\n")
            src_socket.close()
        else:
            self.src_socket, self.src_address = fd.accept()
            select_fd_on("socket", self.src_socket, self.read_socket)

    def clear(self):
        self.queue = queue.Queue()

    ########################################
    # interaction with I/O ports
    ########################################

    def status_checked(self, cpu, in_tight_loop):
        now_time = time.time_ns()

        # mark output ready, to simulate the BAUD rate
        if now_time - self.in_time >= BAUD_NS and not self.queue.empty():
            self.status_device.rx_rdy = True

        out_was_not_ready = not self.status_device.tx_rdy
        # mark input ready, to simulate the BAUD rate
        if now_time - self.out_time >= BAUD_NS:
            self.status_device.tx_rdy = True

        return out_was_not_ready or bool(not self.queue.empty())

    def get_device_input(self, cpu):
        # detect if we're in a tight loop
        elapsed_instr_count = cpu.instr_count - self.prev_instr_count
        self.prev_instr_count = cpu.instr_count
        in_tight_loop = False
        if elapsed_instr_count <= TIGHT_LOOP_LEN:
            if self.in_tight_loop_count < TIGHT_LOOP_COUNT:
                self.in_tight_loop_count += 1
            else:
                in_tight_loop = True
        else:
            self.in_tight_loop_count = 0

        # if this really is a tight loop, chill
        if in_tight_loop:
            if cpu.debug_fh:
                print("SLEEP KEY %04x %d"%(cpu.pc-2, elapsed_instr_count), file=cpu.debug_fh)
            sleep_for_input(SLEEP_FOR_IO)

        key = 0
        if not self.queue.empty():
            key = self.queue.get()

        # mark input not ready, to simulate the BAUD rate
        self.status_device.rx_rdy = False
        self.in_time = time.time_ns()

        # return one key
        return key

    def put_output(self, value):
        # mark output not ready, to simulate the BAUD rate
        self.out_time = time.time_ns()
        self.status_device.tx_rdy = False

        if value == 0xFF:
            return

        if self.src_socket:
            self.src_socket.send(chr(value).encode())

