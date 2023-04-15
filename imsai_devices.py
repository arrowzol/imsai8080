import socket
import select
import signal
import time
import sys
import queue
import curses

DBG_ON = False # "parsed"

def set_baud(baud, bits=7):
    global BAUD_NS
    if baud:
        BAUD_NS = int(1e9/(baud/(bits + 2)))
    else:
        BAUD_NS = 0

set_baud(9600)

TIGHT_LOOP_LEN = 5
TIGHT_LOOP_COUNT = 5
SLEEP_FOR_IO = 0.2

########################################
# select various input sources
########################################

__select_fd = {}
__select_entered = False

def select_fd_on(name, fd, callback):
    global __select_fd

    __select_fd[name] = (fd, callback)

def select_fd_off(name):
    del __select_fd[name]

def sleep_for_input(timeout):
    global __registered_monitor_bell, __executing_monitor, __select_entered
    global __select_fd

    while True:
        # warn: this will not return when ^C is pressed and caught
        __select_entered = True
        rlist, _, _ = select.select(list(fd for fd, callback in __select_fd.values()), [], [], timeout)
        for x in rlist:
            for name, fd_callback in __select_fd.items():
                fd, callback = fd_callback
                if x == fd:
                    callback(name, fd)
                    break
            else:
                print("sleep_for_input error")
        if not __executing_monitor and __registered_monitor_bell:
            __executing_monitor = True
            __select_entered = False
            read_fh = __registered_monitor()
            if read_fh:
                __registered_callback_keyboard(0, read_fh)
            __executing_monitor = False
            __registered_monitor_bell = False
        else:
            __select_entered = False
            break

########################################
# keyboard and the monitor
########################################

__registered_callback_keyboard = None
__registered_callback_keyboard_monitor = None
__registered_monitor = None
__registered_monitor_bell = False
__executing_monitor = False
__select_count_ctrl_c = 0

def __get_deliver_key_to():
    if __executing_monitor:
        return __registered_callback_keyboard_monitor
    else:
        return __registered_callback_keyboard

def __callback_keyboard_stdin(name, fd):
    global __registered_monitor_bell

    deliver_to = __get_deliver_key_to()

    key = ord(fd.read(1))
    if key == 0x1B:
        key = ord(fd.read(1))
        if key == ord('['):
            key = ord(fd.read(1))
            sub_key = 0
            if key == 0x41:
                sub_key = 'N'
            elif key == 0x42:
                sub_key = 'O'
            elif key == 0x43:
                sub_key = 'I'
            elif key == 0x44:
                sub_key = 'H'
            if sub_key:
                deliver_to(ord(sub_key)-0x40)
            else:
                deliver_to(0x1B)
                deliver_to(ord('['))
                deliver_to(key)
        else:
            deliver_to(0x1B)
            deliver_to(key)
    else:
        if __registered_monitor and key == 0x1d:
            __registered_monitor_bell = True
        else:
            deliver_to(key)

def __callback_sigint_handler(sig, frame):
    global __select_count_ctrl_c, __registered_monitor_bell, __executing_monitor

    if sig == 2:
        __select_count_ctrl_c += 1
        if __select_count_ctrl_c >= 6:
            raise Exception("EXIT due to CTRL-C")
        if __select_count_ctrl_c >= 3:
            if __registered_monitor and not __executing_monitor:
                if __select_entered:
                    __registered_monitor_bell = True
                else:
                    __executing_monitor = True
                    read_fh = __registered_monitor()
                    if read_fh:
                        __registered_callback_keyboard(0, read_fh)
                    __executing_monitor = False
            else:
                raise Exception("EXIT due to CTRL-C")
        deliver_to = __get_deliver_key_to()
        deliver_to(3)
    else:
        raise Exception('UNEXPECTED SIG %d'%sig)

def select_keyboard_callback(callback_keyboard, callback_keyboard_monitor, monitor):
    global __registered_callback_keyboard_monitor, __registered_monitor, __registered_callback_keyboard

    __registered_monitor = monitor

    __registered_callback_keyboard = callback_keyboard
    __registered_callback_keyboard_monitor = callback_keyboard_monitor

    select_fd_on("stdin", sys.stdin, __callback_keyboard_stdin)
    signal.signal(signal.SIGINT, __callback_sigint_handler)

def see_cntrl_c():
    global __select_count_ctrl_c
    __select_count_ctrl_c = 0

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
            if cpu.show_inst:
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

class SocketTTYDevice:
    def __init__(self, name, status_device, port):
        self.name = name
        self.status_device = status_device
        self.port = port
        status_device.add_monitored_device(self)

        self.src_socket = None
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(('localhost', port))
        self.server_socket.listen(0x40)
        select_fd_on("svr_socket:%d"%port, self.server_socket, self.callback_accept_socket)

        self.queue = queue.Queue()
        self.in_time = 0
        self.out_time = 0

        self.prev_instr_count = 0
        self.in_tight_loop_count = 0

        self.state = 0
        self.telnet_protocol = False
        self.last_value = 0

        self.x = 0

    def done(self):
        pass

    ########################################
    # socket I/O
    ########################################

    def callback_read_socket(self, name, fd):
        """
        sent by telnet for "mode character":
            xff xfd x03 - "DO", "Supress Go Ahead"
            xff xfd x01 - "DO", "Echo"

        sent by telnet for "mode line":
            xff xfe x03 - "DONT", "Supress Go Ahead"
            xff xfb x22 - "WILL", "linemode"
            xff xfe x01 - "DONT", "Echo"

        send ^C as:
            xff xf4 - Interrupt Process
            xff xfd x06 - ^C key
        ^O not sent
        send ^M as: x0d x00

        UP: x1b x5b x41
        DN: x1b x5b x42
        RT: x1b x5b x43
        LF: x1b x5b x44

        """
        buffer = self.src_socket.recv(4096)
        if len(buffer) == 0:
            self.src_socket.close()
            self.src_socket = None
            select_fd_off("socket:%d"%self.port)
        for c in buffer:
            if DBG_ON == "raw":
                print("READ %s %02x %d"%(self.name, c, c))

            if self.state == 0:
                if c == 0xFF:
                    self.state = 1
                elif c == 0x1B:
                    self.state = 10
                elif 0 < c < 0x80:
                    if DBG_ON == "parsed":
                        print("READ %s %02x (%s)"%(self.name, c, repr(chr(c))[1:-1]))

                    # uppercase
                    if ord('a') <= c <= ord('z'):
                        c -= 0x20
                    self.queue.put(c)
                elif DBG_ON == "parsed":
                    print("READ %s %02x"%(self.name, c))

            elif self.state == 1:
                if 0xFB <= c:
                    self.telnet_cmd = c
                    self.state = 2
                elif 0xF0 <= c:
                    self.state = 0
                    if DBG_ON == "parsed":
                        print("READ %s TELNET-CMD %02x"%(self.name, c))
                else:
                    self.state = 0

            elif self.state == 2:
                if self.telnet_cmd == 0xFD and 0x06 <= c <= 0x10:
                    self.queue.put(c)
                if DBG_ON == "parsed":
                    s_telnet_cmd = "%02x"%self.telnet_cmd
                    i = self.telnet_cmd - 0xfb
                    if 0 <= i < 4:
                        s_telnet_cmd = "CMD_" + ["WILL", "WOUN'T", "DO", "DON'T"][i]
                    print("READ %s TELNET-CMD %s %02x"%(self.name, s_telnet_cmd, c))
                self.state = 0
                self.telnet_protocol = True
            elif self.state == 10:
                if c == 0x5B:
                    self.state = 11
                else:
                    self.state = 0
            elif self.state == 11:
                if c == 0x41:
                    self.queue.put(ord('N')-0x40)
                elif c == 0x42:
                    self.queue.put(ord('O')-0x40)
                elif c == 0x43:
                    self.queue.put(ord('I')-0x40)
                elif c == 0x44:
                    self.queue.put(ord('H')-0x40)
                self.state = 0
            else:
                self.state = 0

    def callback_accept_socket(self, name, fd):
        if self.src_socket:
            src_socket, src_address = fd.accept()
            src_socket.send(b"connection already exists\n")
            src_socket.close()
        else:
            self.src_socket, self.src_address = fd.accept()
            select_fd_on("socket:%d"%self.port, self.src_socket, self.callback_read_socket)
            self.setup_telnet_chars()

    def setup_telnet_linemode(self):
        self.src_socket.send(b"\xff\xfe\03")
        self.src_socket.send(b"\xff\xfe\01")

    def setup_telnet_chars(self):
        self.src_socket.send(b"\xff\xfd\03")
        self.src_socket.send(b"\xff\xfd\01")

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
        if cpu.pc - 2 == 0x000f:
            time.sleep(0.5)
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
        else:
            sleep_for_input(0.001)

        if not self.queue.empty():
            key = self.queue.get()
            self.last_value = key
        else:
            key = self.last_value

        # mark input not ready, to simulate the BAUD rate
        self.status_device.rx_rdy = False
        self.in_time = time.time_ns()

        # return one key
        return key

    def put_output(self, c):
        if DBG_ON:
            print("WRITE %s %02x (%s)"%(self.name, c, repr(chr(c))[1:-1]))

        # mark output not ready, to simulate the BAUD rate
        self.out_time = time.time_ns()
        self.status_device.tx_rdy = False

        if c == 0xFF:
            return

        if self.src_socket:
            self.src_socket.send(c.to_bytes(1, 'big'))

class ConstantInputDevice:
    def __init__(self, value):
        self.value = value

    def get_device_input(self, cpu):
        return self.value

class CursesMonitorIO:
    def __init__(self, stdwin, parent):
        self.stdwin = stdwin
        self.parent = parent
        self.keys = []
        self.cr = 0
        self.print('\n')
        self.cntl_c_event = False

    def log(self, msg):
        self.parent.log(msg)

    def print(self, msg):
        self.stdwin.addstr(msg, curses.color_pair(1))
        self.stdwin.refresh()

    def readline(self):
        while not self.cr:
            sleep_for_input(SLEEP_FOR_IO)
        if self.cntl_c_event:
            see_cntrl_c()
            self.cntl_c_event = False
            return "\x03"
        line = "".join(self.keys[:self.cr])
        self.keys = self.keys[self.cr+1:]
        self.cr = 0
        return line

    def callback_keyboard(self, key):
        if key == 3:
            self.cntl_c_event = True
            return

        c = chr(key)
        self.keys.append(c)

        # LF signals EOL
        if key == 0x0d:
            self.cr = len(self.keys)-1

        # convert CR to LF before printing
        if key == 0x0d:
            c = '\n'

        self.print(c)

#
# IMSAI VIO
#
# for 32x32:
#   14 <- x81
#   15 <- x30
#
# for 64x64:
#   14 <- x84
#   15 <- xb0
#
hx = "0123456789abcdef"
class CursesDevice():
    def __init__(self, name, status_device, vio=False, monitor=None):
        self.name = name
        self.status_device = status_device
        self.vio = vio
        self.monitor = monitor
        status_device.add_monitored_device(self)

        ########################################
        # setup curses
        ########################################

        self.stdscr = curses.initscr()
        curses.start_color()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_RED)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_BLUE)
        curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_WHITE)

        right_indent = 0
        screen_width = 80
        log_lines = 10

        if self.vio:
            right_indent = 4 + 64 + 2
            for line in range(64):
                self.stdscr.addstr(line, 0, "%02x"%line)

        self.log_win = curses.newwin(log_lines, screen_width, 2, right_indent)
        self.log_win.scrollok(True)

        self.chanel_a_win = curses.newwin(curses.LINES - log_lines-4, screen_width, log_lines+4, right_indent)
        self.chanel_a_win.scrollok(True)
        self.chanel_a_win.addstr('Press ^] to exit the sim\n\n')
        self.chanel_a_win.refresh()

        ########################################
        # setup keyboard
        ########################################

        self.queue = queue.Queue()
        self.in_time = 0
        self.out_time = 0
        self.prev_instr_count = 0
        self.in_tight_loop_count = 0
        self.last_value = 0
        self.monitor_io = None
        self.read_fh = None

        self.count_ctrl_c = 0

        monitor_io = CursesMonitorIO(self.chanel_a_win, self)
        def my_monitor():
            if monitor:
                return monitor(monitor_io)
            else:
                raise Exception('there is no monitor')

        select_keyboard_callback(self.callback_keyboard, monitor_io.callback_keyboard, my_monitor)

    def log(self, msg):
        self.log_win.addstr("\n")
        self.log_win.addstr(msg)
        self.log_win.refresh()

    def done(self):
        if self.stdscr:
            curses.nocbreak()
            self.stdscr.keypad(False)
            curses.echo()
            curses.endwin()
            self.stdscr = None

    ########################################
    # 64x64 memory mapped "monitor"
    ########################################

    def set_mem(self, addr, old_value, new_value):
        if self.vio:
            addr -= 0x800
            l1 = addr // 16
            c1 = (addr % 16)*2
            bank = l1 // 32

            c1 += 32 * (bank & 1)
            l1 -= 32 * ((bank+1)//2)

            self.spot(l1, c1, new_value & 0x0F)
            self.spot(l1, c1+1, (new_value >> 4) & 0x0F)
            self.stdscr.refresh()

    def spot(self, line, col, value):
        # _001 = RED
        # _010 = GREEN
        # _100 = BLUE
        color = 0
        if value == 9:      # 1001 RED
            color = 1
        elif value == 10:   # 1010 GREEN
            color = 2
        elif value == 12:   # 1100 BLUE
            color = 3
        elif value == 15:   # 1111 WHITE ?
            color = 4

        self.stdscr.addstr(line, 3+col, hx[value], curses.color_pair(color))

    ########################################
    # interaction with I/O ports
    ########################################

    def callback_keyboard(self, key, read_fh=None):
        if read_fh:
            if self.read_fh:
                self.read_fh.close()
            self.read_fh = read_fh
            return

        # uppercase
        if ord('a') <= key <= ord('z'):
            key -= 0x20
        self.queue.put(key)

    def status_checked(self, cpu, in_tight_loop):
        now_time = time.time_ns()

        # mark output ready, to simulate the BAUD rate
        if now_time - self.in_time >= BAUD_NS and (not self.queue.empty() or self.read_fh):
            self.status_device.rx_rdy = True

        out_was_not_ready = not self.status_device.tx_rdy
        # mark input ready, to simulate the BAUD rate
        if now_time - self.out_time >= BAUD_NS:
            self.status_device.tx_rdy = True

        return out_was_not_ready or bool(not self.queue.empty() or self.read_fh)

    def get_device_input(self, cpu):
        if self.count_ctrl_c:
            self.count_ctrl_c = 0
            return 3

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
        else:
            sleep_for_input(0.001)

        key = -1
        if self.read_fh:
            c = self.read_fh.read(1)
            if len(c) == 0:
                self.read_fh.close()
                self.read_fh = None
                self.chanel_a_win.addstr('\n---read done ---\n', curses.color_pair(1))
                self.chanel_a_win.refresh()
            else:
                key = ord(c)

                # convert CR to LF
                if key == 0x0a:
                    key = 0x0d

                self.log("FILE-IN %02x (%s)"%(key, repr(c)[1:-1]))

        if key != -1:
            pass
        elif not self.queue.empty():
            key = self.queue.get()
            self.last_value = key
        else:
            key = self.last_value

        # mark input not ready, to simulate the BAUD rate
        self.status_device.rx_rdy = False
        self.in_time = time.time_ns()

        if key == 3:
            see_cntrl_c()

        # return one key
        return key

    def put_output(self, c):
        # mark output not ready, to simulate the BAUD rate
        self.out_time = time.time_ns()
        self.status_device.tx_rdy = False

        if c == 0xFF:
            return

        s = chr(c)
        if 0 < c < 0x80 and s != '\r':
            self.chanel_a_win.addstr(s)
            self.chanel_a_win.refresh()

class DeviceFactory:
    def __init__(self):
        self.in_devices = {}
        self.out_devices = {}

    def get_out_device(self, device_id):
        out_device = self.out_devices.get(device_id, None)
        if not out_device:
            pass
            # print("MISSING OUT DEVICE x%02x"%device_id)
        return out_device

    def get_in_device(self, device_id):
        in_device = self.in_devices.get(device_id, None)
        if not in_device:
            pass
            # print("MISSING IN DEVICE x%02x"%device_id)
        return in_device

    def add_input_device(self, device_id, device):
        self.in_devices[device_id] = device

    def add_output_device(self, device_id, device):
        self.out_devices[device_id] = device


#
# SIO2
#
#

