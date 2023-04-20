import select
import signal
import sys

SLEEP_FOR_IO = 0.2

########################################
# basic select for I/O
########################################

__select_fd = {}
__select_entered = False

def select_fd_on(name, fd, callback):
    global __select_fd

    __select_fd[name] = (fd, callback)

def select_fd_off(name):
    del __select_fd[name]

def sleep_for_input(timeout):
    global __monitor_bell, __executing_monitor, __select_entered
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
        if not __executing_monitor and __monitor_bell:
            __select_entered = False
            run_monitor()
            __monitor_bell = False
        else:
            __select_entered = False
            break

########################################
# keyboard and the monitor
########################################

__uppercase_keys = False
__callback_keyboard_dict = {}
__callback_keyboard = None
__monitor_func = None
__monitor_bell = False
__executing_monitor = False
__select_count_ctrl_c = 0
__curses_on = False

def __get_deliver_key_to():
    if __executing_monitor:
        return __monitor_keyboard.callback_keyboard
    else:
        return __callback_keyboard

def __callback_keyboard_stdin(name, fd):
    global __monitor_bell

    deliver_to = __get_deliver_key_to()

    if __curses_on:
        key = ord(fd.read(1))
        if key == 0x1B and i < len(keys):
            key = ord(fd.read(1))
            if key == ord('[') and i < len(keys):
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
            if __monitor_func and key == 0x1d:
                __monitor_bell = True
            else:
                deliver_to(key)
    else:
        keys = fd.readline()
        if not keys:
            raise Exception('EOF from stdin')
        if __uppercase_keys:
            keys = keys.upper()
        keys = keys.rstrip() + "\r\n"
        for key in keys:
            deliver_to(ord(key))

def __callback_sigint_handler(sig, frame):
    global __select_count_ctrl_c, __monitor_bell, __executing_monitor

    if sig == 2:
        __select_count_ctrl_c += 1
        if __select_count_ctrl_c >= 6:
            raise Exception("EXIT due to CTRL-C")
        if __select_count_ctrl_c >= 3:
            if __monitor_func and not __executing_monitor:
                if __select_entered:
                    __monitor_bell = True
                else:
                    run_monitor()
            else:
                raise Exception("EXIT due to CTRL-C")
        deliver_to = __get_deliver_key_to()
        deliver_to(3)
    else:
        raise Exception('UNEXPECTED SIG %d'%sig)

def register_monitor(monitor_func, monitor_box):
    global __monitor_func, __monitor_box, __monitor_keyboard

    monitor_box.print("MONITOR IS HERE\n", 2)
    __monitor_func = monitor_func
    __monitor_box = monitor_box
    __monitor_keyboard = MonitorKeyboard(monitor_box)

def register_keyboard_callback(name, callback_keyboard):
    global __callback_keyboard, __callback_keyboard_dict

    __callback_keyboard_dict[name] = callback_keyboard
    if not __callback_keyboard:
        __callback_keyboard = callback_keyboard

    select_fd_on("stdin", sys.stdin, __callback_keyboard_stdin)
    signal.signal(signal.SIGINT, __callback_sigint_handler)

def get_keyboard_names(filter_func=None):
    if not filter_func:
        filter_func = lambda name: True
    return list(filter(filter_func, __callback_keyboard_dict.keys()))

def set_keyboard_focus(name):
    global __callback_keyboard
    kbd = __callback_keyboard_dict.get(name, None)
    if kbd:
        __callback_keyboard = kbd

def ate_cntrl_c():
    global __select_count_ctrl_c
    __select_count_ctrl_c = 0

class MonitorKeyboard:
    def __init__(self, display_box):
        self.display_box = display_box
        self.cntl_c_event = False
        self.keys = []
        self.cr = 0

    def readline(self):
        while not self.cr:
            sleep_for_input(SLEEP_FOR_IO)
        if self.cntl_c_event:
            ate_cntrl_c()
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

        self.display_box.print(c)

def run_monitor(message=None):
    global __executing_monitor
    if __monitor_func:
        if message:
            __monitor_box.print(message + "\n", 2)
        __executing_monitor = True
        read_fh = __monitor_func(__monitor_keyboard, __monitor_box)
        if read_fh:
            __callback_keyboard(0, read_fh)
        __executing_monitor = False

########################################
# curses setup
########################################

import curses
from curses.textpad import Textbox, rectangle

class DisplayBox:
    def __init__(self, win):
        self.win = win
        self.refresh = True
        self.color = 0

    def refresh_on(self):
        self.refresh = True
        self.win.refresh()

    def set_color(self, color):
        old_color = self.color
        self.color = color
        return old_color

    def refresh_off(self):
        self.refresh = False

    def print(self, string, color=-1):
        if color == -1:
            color = self.color
        if color:
            self.win.addstr(string, curses.color_pair(color))
        else:
            self.win.addstr(string)
        if self.refresh:
            self.win.refresh()

    def print_xy(self, row, col, string, color=-1):
        if color == -1:
            color = self.color
        if color:
            self.win.addstr(row, col, string, curses.color_pair(color))
        else:
            self.win.addstr(row, col, string)
        if self.refresh:
            self.win.refresh()

def curses_init():
    global __stdscr, __curses_on
    __curses_on = True
    __stdscr = curses.initscr()
    curses.start_color()
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_RED)
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_GREEN)
    curses.init_pair(5, curses.COLOR_BLUE, curses.COLOR_BLACK)
    curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_BLUE)
    curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_WHITE)

def curses_done():
    global __stdscr
    curses.nocbreak()
    __stdscr.keypad(False)
    curses.echo()
    curses.endwin()

def curses_get_box(rows, columns, row, column, title=None):
    global __stdscr
    box_win = curses.newwin(rows, columns, row, column)

    # draw border
    for r in range(rows):
        __stdscr.addstr(row + r,    column-1, '|')
        __stdscr.addstr(row + r,    column+columns, '|')
    for c in range(columns):
        __stdscr.addstr(row - 1,    column + c, '-')
        __stdscr.addstr(row + rows, column + c, '-')
    if title:
        __stdscr.addstr(row - 1,    column + 2, " " + title + " ", curses.color_pair(4))

    __stdscr.addstr(    row - 1,    column-1, '+')
    __stdscr.addstr(    row - 1,    column+columns, '+')
    __stdscr.addstr(    row + rows, column-1, '+')
    __stdscr.addstr(    row + rows, column+columns, '+')

    __stdscr.refresh()
    box_win.scrollok(True)
    return DisplayBox(box_win)

__log_box = None

def curses_create_log_box(rows, columns, row, column):
    global __log_box
    __log_box = curses_get_box(rows, columns, row, column, "LOG")

__log_fhs = []

def add_log_file(log_fh):
    __log_fhs.append(log_fh)

def log(message, color=-1):
    global __log_box
    if __log_box:
        __log_box.print("\n" + message, color)
    elif __curses_on:
        print("LOG: " + message + "\n")
    for log_fh in __log_fhs:
        print("LOG: " + message + "\n", file=log_fh)

########################################
# life without curses
########################################

class StdoutBox:
    def refresh_on(self):
        pass

    def set_color(self, color):
        return 0

    def refresh_off(self):
        pass

    def print(self, string, color=-1):
        sys.stdout.write(string);
        sys.stdout.flush()

    def print_xy(self, row, col, string, color=-1):
        raise Exception('print_xy not supported')

def get_stdout_box():
    return StdoutBox()

