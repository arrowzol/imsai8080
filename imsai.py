#!/usr/bin/python3

import sys
import curses

import abstract_io
import intel8080
import imsai_devices
import imsai_disk
import imsai_hex

do_socket_1 = False
do_asm_debug = False
run_basic = None
hex_file = None
basic_4k = False
do_mem = 64
dsk_file = []
do_vio = False
do_curses = False

for arg in sys.argv[1:]:
    if arg == "-a":
        do_asm_debug = True
    elif arg.startswith("-m="):
        do_mem = int(arg[3:])
        if not (0 < do_mem <= 64):
            print("invalid memory")
            sys.exit(1)
    elif arg == "-v":
        do_vio = True
    elif arg == "-c":
        do_curses = True
    elif arg == "-s":
        do_socket_1 = True
    elif arg == "-4":
        basic_4k = True
    elif arg.lower().endswith('.bas'):
        run_basic = arg
    elif arg.lower().endswith('.hex'):
        hex_file = arg
    elif arg.lower().endswith('.dsk'):
        dsk_file.append(arg)

device_factory = imsai_devices.DeviceFactory()
cpu = intel8080.CPU8080(device_factory, do_mem*1024)

########################################
# load memory
########################################

disk_device = None
if dsk_file:
    disk_device = imsai_disk.DiskDevice(dsk_file)
    disk_device.boot(cpu)
    device_factory.add_output_device(0xFD, disk_device)
elif hex_file:
    imsai_hex.HexLoader(hex_file).boot(cpu)
elif basic_4k:
    print("USING 4K BASIC")
    imsai_hex.HexLoader('IMSAI/basic4k.hex').boot(cpu)
    cpu.extend_symbol('IOBUF', -2)
    cpu.extend_symbol('BEGPR', -2)
else:
    print("USING 8K BASIC")
    imsai_hex.HexLoader('IMSAI/basic8k.hex').boot(cpu)
    cpu.extend_symbol('BEGPR', 250)
    cpu.set_read_only_end('RAM')

########################################
# setup devices
########################################

def monitor_func(keyboard, display_box):
    old_color = display_box.set_color(1)
    display_box.print("\n--(monitor-begin)--\n")
    while True:
        display_box.print('M> ')
        line = keyboard.readline()
        if line == 'x' or line == 'exit':
            display_box.print("--(monitor-end)--\n")
            break
        elif line == 'bye':
            raise Exception('Monitor Commanded bye')
        elif line == 'tron':
            cpu.tron()
        elif line == 'troff':
            cpu.troff()
        elif line.startswith('dump '):
            try:
                addr = int(line[5:], 16)
                for i in range(16):
                    for j in range(16):
                        display_box.print("%02x "%cpu.mem[addr+i*16+j])
                    display_box.print("\n")
            except Exception:
                display_box.print("error")
        elif line.startswith('baud '):
            try:
                baud_rate = int(line[5:])
                imsai_devices.set_baud(baud_rate)
                display_box.print("set baud rate to %d\n"%(baud_rate))
            except Exception:
                display_box.print("error")
        elif line.startswith('read '):
            fn = line[5:]
            try:
                fh = open(fn, 'rb')
                display_box.print('reading %s\n'%fn)
                return fh
            except Exception:
                display_box.print('error opening file %s'%(fn))
        elif line == 's' or line == 'status':
            display_box.print('PC: %04x\n'%(cpu.pc))
            display_box.print('SP: %04x\n'%(cpu.sp))
            for i in range(-5,5):
                display_box.print('  %04x %s\n'%(cpu.pc+i, cpu.addr_to_str(cpu.pc+i)))
        elif line == 'help':
            display_box.print('cmds:\n')
            display_box.print('  baud <#>\n')
            display_box.print('  s|status\n')
            display_box.print('  x|exit\n')
    display_box.set_color(old_color)

serial_status_chanel_a = imsai_devices.StatusSerialDevice()
device_factory.add_input_device(3, serial_status_chanel_a)

serial_status_chanel_b = imsai_devices.StatusSerialDevice()
# TODO: I'm not sure about the device id
device_factory.add_input_device(5, serial_status_chanel_b)

in_chanel_a = None
out_chanel_a = None
try:
    if run_basic:
        in_chanel_a = imsai_devices.ScriptedSerialInputDevice("Channel A", serial_status_chanel_a, cpu)
        out_chanel_a = imsai_devices.ConsoleOutputDevice("Channel A", serial_status_chanel_a)
        imsai_devices.set_baud(0)

        in_chanel_a.load_file(run_basic)
        cpu.limit_steps = 5000000
        if basic_4k:
            hex_file = 'IMSAI/basic4k.hex'
        else:
            hex_file = 'IMSAI/basic8k.hex'

    elif do_curses:
        right_indent = 0
        screen_width = 80
        log_lines = 10

        abstract_io.curses_init()
        abstract_io.curses_create_log_box(log_lines, screen_width, 2, right_indent)

        if do_vio:
            right_indent = 3+64 + 2
            vio_box = abstract_io.curses_get_box(64, 3+64, 0, 0)
            vio_box.refresh_off()
            for line in range(64):
                vio_box.print("\n%02x"%line)
            vio_box.refresh_on()
            imsai_devices.VIODevice(device_factory, cpu, vio_box)

        chanel_a_box = abstract_io.curses_get_box(curses.LINES - log_lines-4, screen_width, log_lines+4, right_indent)

        in_chanel_a = imsai_devices.KeyboardToSerialDevice('Channel A', serial_status_chanel_a, chanel_a_box)
        out_chanel_a = in_chanel_a

        abstract_io.register_monitor(monitor_func, chanel_a_box)
        chanel_a_box.print('Press ^] to enter the monitor\n\n', 1)

    elif do_socket_1:
        in_chanel_a = imsai_devices.SocketToSerialDevice("Socket Channel A", serial_status_chanel_a, 8008)
        out_chanel_a = in_chanel_a

    #    chanel_b = imsai_devices.SocketToSerialDevice("Socket Channel B", serial_status_chanel_b, 8009)
    #    device_factory.add_input_device(0x0E, chanel_b)
    #    device_factory.add_output_device(0x0E, chanel_b)

        in_x = imsai_devices.ConstantInputDevice(0x7E)
        device_factory.add_input_device(0xFF, in_x)
    else:
        chanel_a_box = abstract_io.get_stdout_box()
        in_chanel_a = imsai_devices.KeyboardToSerialDevice('Channel A', serial_status_chanel_a, chanel_a_box)
        out_chanel_a = in_chanel_a

    device_factory.add_input_device(2, in_chanel_a)
    device_factory.add_output_device(2, out_chanel_a)

    ########################################
    # set debug options
    ########################################

    if do_asm_debug:
        cpu.show_inst = True
        cpu.show_mem_set = True
        cpu.show_mem_get = True

    ########################################
    # run, starting at addr 0
    ########################################

    if do_socket_1:
        do_run = False
        def keyboard(name, fd):
            global do_run
            keys = fd.readline().rstrip()
            if keys == 'run':
                do_run = True
            elif keys.startswith('baud '):
                baud = int(keys[5:])
                print('setting BAUD rate to %d'%baud)
                imsai_devices.set_baud(baud)
            elif keys == 'line':
                in_chanel_a.setup_telnet_linemode()
            elif keys == 'char':
                in_chanel_a.setup_telnet_chars()
        imsai_devices.select_fd_on("stdin", sys.stdin, keyboard)

        while not do_run:
            imsai_devices.sleep_for_input(2)

        print("RUNNING")
        in_chanel_a.clear()

    cpu.reset(0)
    cpu.run()
finally:
    if do_curses:
        abstract_io.curses_done()
    if in_chanel_a:
        in_chanel_a.done()
    if out_chanel_a:
        out_chanel_a.done()

# see IMSAI/basic4k.hex
# see IMSAI/basic4k.asm
# see IMSAI/basic4k.symbols

# see IMSAI/basic8k.hex
# see IMSAI/basic8k.asm

