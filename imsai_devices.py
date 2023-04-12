import socket
import select
import signal
import time
import sys
import queue

KEY_SPEED_MS = 10

select_fd = {}

def select_fd_on(name, fd, callback):
    select_fd[name] = (fd, callback)

def select_fd_off(name):
    del select_fd[name]

def sleep_for_input(timeout):
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
        sleep_for_input(2)

class StatusDevice:
    """The TTY reports its TX and RX status through this device"""
    def __init__(self):
        self.tx_rdy = True
        self.rx_rdy = False

        self.monitored_devices = []
        self.prev_instr_count = 0
        self.in_tight_loop_count = 0

    def add_monitored_device(self, device):
        self.monitored_devices.append(device)

    def get_device_input(self, cpu):
        elapsed_instr_count = cpu.instr_count - self.prev_instr_count
        self.prev_instr_count = cpu.instr_count
        pc = cpu.pc-2

        # detect if we're in a tight loop
        in_tight_loop = False
        if elapsed_instr_count <= 3:
            if self.in_tight_loop_count < 5:
                self.in_tight_loop_count += 1
            else:
                in_tight_loop = True
        else:
            self.in_tight_loop_count = 0

        # notify devices they are being monitored, let them take action to exit the tight loop
        for device in self.monitored_devices:
            if device.status_checked(in_tight_loop):
                in_tight_loop = False

        # if this really is a tight loop, chill
        if in_tight_loop:
            if cpu.debug_fh:
                print("SLEEP %04x %d"%(pc, elapsed_instr_count), file=cpu.debug_fh)
            time.sleep(0.2)

        # return the status
        return self.tx_rdy * 0x01 | self.rx_rdy * 0x02

class ScriptedInputDevice:
    def __init__(self, name, status_device, out_device):
        self.name = name
        self.status_device = status_device
        self.out_device = out_device

        status_device.add_monitored_device(self)
        self.stack = None
        self.ck_cnt = 0

    def load_file(self, text_file_name):
        fh = open(text_file_name)
        string = "TAPE\rNEW\r" + "\r".join((line.strip() for line in fh)) + "\rKEY\rRUN\r"
        self.stack = [ord(c) for c in string]
        self.stack.reverse()
        fh.close()

    def status_checked(self, in_tight_loop):
        if in_tight_loop:
            self.ck_cnt = 0
        if self.ck_cnt:
            self.ck_cnt -= 1
        elif self.stack or self.out_device.is_done():
            self.status_device.rx_rdy = True
            return True

    def get_device_input(self, cpu):
        if self.out_device.is_done():
            return -1
        self.status_device.rx_rdy = False
        self.ck_cnt = 10
        if self.stack:
            return self.stack.pop()
        print("ERROR1")
        sys.exit(1)

    def done(self):
        if self.stack:
            print("Remaining unread chars: %d"%len(self.stack))

class InteractiveInputDevice:
    def __init__(self, name, cpu, status_device):
        self.name = name
        self.cpu = cpu
        self.status_device = status_device

        status_device.add_monitored_device(self)
        self.stack = None
        signal.signal(signal.SIGINT, self.sigint_handler)
        self.count_ctrl_c = 0
        self.key_check_time = 0
        self.key_returned_time = 0
        self.halt = False

    def status_checked(self, in_tight_loop):
        # delay 100ms before allowing another key
        if time.time_ns() - self.key_returned_time < 1000000 * KEY_SPEED_MS:
            return bool(self.stack)

        # if there is another key, signal RX ready
        if self.stack:
            if not self.status_device.rx_rdy:
                self.status_device.rx_rdy = True
            return True

        # really check stdin for keys every second, to make the simulator not block
        if time.time_ns() - self.key_check_time < 1000000000:
            return False

        self.key_check_time = time.time_ns()

        # if no keys buffered, read real keyboard and queue up key strokes
        rlist, _, _ = select.select([sys.stdin], [], [], 0.001)
        if rlist:
            string = sys.stdin.readline().upper()
            # pressing ^D will produce empty string
            if not string:
                self.halt = True
            else:
                string = string.strip()

                # not sure what this does, toggles OUTSW variable
                if string == 'CTRLO':
                    print("SEND ^O")
                    self.stack = [0x0F]
                elif string.startswith('X '):
                    addr = string[5:]
                    addr = self.cpu.addr_to_number(addr)
                    print("mem(%04x) = %02x"%(addr, self.cpu.get_mem(addr)))
                else:
                    self.stack = [ord(c) for c in string]
                    self.stack.append(ord('\r'))
                    self.stack.reverse()
                    self.status_device.rx_rdy = True
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
        if self.halt:
            return -1
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
        self.key_returned_time = time.time_ns()

        # return one key
        return key

    def done(self):
        pass

class TTYOutputDevice:
    def __init__(self, name):
        self.name = name
        self.out_chars = []
        self.out_lines = []
        self.is_done_found = False

    def put_output(self, value):
        if value == 0x0a:
            return
        if value == 0x0d:
            line = "".join(self.out_chars)
            if line == "BYE BYE":
                self.is_done_found = True
            self.out_lines.append(line)
            self.out_chars = []
        elif 0 < value < 127:
            self.out_chars.append(chr(value))

    def is_done(self):
        return self.is_done_found

    def done(self):
        if self.out_chars:
            self.out_lines.append("".join(self.out_chars))
        print("\n".join(self.out_lines))

class InteractiveOutputDevice:
    def __init__(self, name, status_device):
        self.name = name
        self.status_device = status_device
        self.line = []
        self.is_done_found = False

    def put_output(self, c):
        # note: INIT1 called, which sends xae x40 xba x37
        if 0 < c < 127:
            print(chr(c), end='', flush=True)
        if 32 <= c < 127:
            self.line.append(chr(c))
        if c == 0x0d:
            line = "".join(self.line)
            if line == "BYE BYE":
                self.is_done_found = True
            self.line = []

    def is_done(self):
        return self.is_done_found

    def done(self):
        pass

class SocketTtyDevice:
    def read_socket(self, name, fd):
        buffer = self.src_socket.recv(4096)
        if len(buffer) == 0:
            self.src_socket.close()
            self.src_socket = None
            select_fd_off("socket")
        for c in buffer:
            if 0 < c < 0x80:
                if ord('a') <= c <= ord('z'):
                    c -= 0x20
                print("KEY %02x"%c)
                self.queue.put(c)

    def accept_socket(self, name, fd):
        if self.src_socket:
            src_socket, src_address = fd.accept()
            src_socket.send(b"connection already exists\n")
            src_socket.close()
        else:
            self.src_socket, self.src_address = fd.accept()
            select_fd_on("socket", self.src_socket, self.read_socket)

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
        self.key_check_time = 0
        self.key_returned_time = 0

    def clear(self):
        self.queue = queue.Queue()

    def status_checked(self, in_tight_loop):
        if in_tight_loop and not self.queue.empty():
            in_tight_loop = False

        # delay 100ms before allowing another key
        if time.time_ns() - self.key_returned_time < 1000000 * KEY_SPEED_MS:
            return bool(not self.queue.empty())

        sleep_for_input([0.001, 2][in_tight_loop])

        # if there is another key, signal RX ready
        if not self.queue.empty():
            if not self.status_device.rx_rdy:
                self.status_device.rx_rdy = True
            return True

        # really check stdin for keys every second, to make the simulator not block
        if time.time_ns() - self.key_check_time < 1000000000:
            return False

        self.key_check_time = time.time_ns()
        return bool(not self.queue.empty())

    def get_device_input(self, cpu):
        key = 0
        if not self.queue.empty():
            key = self.queue.get()

        # mark keyboard not ready, record when this key was returned
        self.status_device.rx_rdy = False
        self.key_returned_time = time.time_ns()

        # return one key
        if key == 0:
            print("FAIL")
            sys.exit(1)
        return key

    def put_output(self, value):
        if value == 0xFF:
            return
        if self.src_socket:
            self.src_socket.send(chr(value).encode())

    def done(self):
        pass

