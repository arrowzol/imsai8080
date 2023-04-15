#!/usr/bin/python3

import os
import sys

import intel8080
import imsai_devices

do_socket = False
do_debug = False
run_basic = None
hex_file = None
basic_4k = False
do_mem = 16
do_vio = False
for arg in sys.argv[1:]:
    if arg == "-d":
        do_debug = True
    elif arg.startswith("-m="):
        do_mem = int(arg[3:])
        if not (0 < do_mem <= 64):
            print("invalid memory")
            sys.exit(1)
    elif arg == "-v":
        do_vio = True
    elif arg == "-s":
        do_socket = True
    elif arg == "-4":
        basic_4k = True
    elif arg.lower().endswith('.bas'):
        run_basic = arg
    elif arg.lower().endswith('.hex'):
        hex_file = arg

device_factory = imsai_devices.DeviceFactory()
cpu = intel8080.CPU8080(device_factory, do_mem*1024)

########################################
# load program BASIC
########################################

if hex_file:
    sym_file = hex_file[:-3] + 'symbols'
    asm_file = hex_file[:-3] + 'asm'
    if os.path.exists(asm_file):
        cpu.read_asm(asm_file)
    if os.path.exists(sym_file):
        cpu.read_symbols(sym_file)
    cpu.read_hex(hex_file)
elif basic_4k:
    print("USING 4K BASIC")
    cpu.read_asm('IMSAI/basic4k.asm')
    cpu.read_symbols('IMSAI/basic4k.symbols')
    cpu.read_hex('IMSAI/basic4k.hex')
    cpu.extend_symbol('IOBUF', -2)
    cpu.extend_symbol('BEGPR', -2)
else:
    print("USING 8K BASIC")
    cpu.read_asm('IMSAI/basic8k.asm')
    cpu.read_hex('IMSAI/basic8k.hex')
    cpu.extend_symbol('BEGPR', 250)
    cpu.set_read_only_end('RAM')

########################################
# setup devices
########################################

class Monitor:
    def go(self, stdio):
        stdio.log('yea')
        stdio.print("\n--(monitor-begin)--\n")
        while True:
            stdio.print('M> ')
            line = stdio.readline()
            if line == 'x' or line == 'exit':
                stdio.print("--(monitor-end)--\n")
                break
            elif line.startswith('baud '):
                imsai_devices.set_baud(int(line[5:]))
            elif line.startswith('read '):
                fn = line[5:]
                try:
                    fh = open(fn, 'rb')
                    return fh
                except Exception:
                    stdio.print('error opening file %s'%(fn))
            elif line == 's' or line == 'status':
                stdio.print('PC: %04x\n'%(cpu.pc))
                stdio.print('SP: %04x\n'%(cpu.sp))
                for i in range(-5,5):
                    stdio.print('  %04x %s\n'%(cpu.pc+i, cpu.addr_to_str(cpu.pc+i)))
            elif line == 'help':
                stdio.print('cmds:\n')
                stdio.print('  baud <#>\n')
                stdio.print('  s|status\n')
                stdio.print('  x|exit\n')

status_channel_a = imsai_devices.StatusDevice()

if run_basic:
    in_channel_a = imsai_devices.ScriptedInputDevice("Channel A", status_channel_a, cpu)
    out_channel_a = imsai_devices.ConsoleOutputDevice("Channel A", status_channel_a)
    imsai_devices.set_baud(0)

    in_channel_a.load_file(run_basic)
    cpu.limit_steps = 5000000
    if basic_4k:
        hex_file = 'IMSAI/basic4k.hex'
    else:
        hex_file = 'IMSAI/basic8k.hex'
elif do_socket:
    in_channel_a = imsai_devices.SocketTTYDevice("Socket Channel A", status_channel_a, 8008)
    out_channel_a = in_channel_a

#    status_channel_b = imsai_devices.StatusDevice()
#    channel_b = imsai_devices.SocketTTYDevice("Socket Channel B", status_channel_b, 8009)
#    device_factory.add_input_device(0x0F, status_channel_b)
#    device_factory.add_input_device(0x0E, channel_b)
#    device_factory.add_output_device(0x0E, channel_b)

    in_x = imsai_devices.ConstantInputDevice(0x7E)
    device_factory.add_input_device(0xFF, in_x)
else:
    curses_device = imsai_devices.CursesDevice('Channel A', status_channel_a, do_vio, Monitor())
    cpu.set_mem_device(curses_device, 0x0800, 0x1000)
    in_channel_a = curses_device
    out_channel_a = curses_device

device_factory.add_input_device(3, status_channel_a)
device_factory.add_input_device(2, in_channel_a)
device_factory.add_output_device(2, out_channel_a)

########################################
# set debug options
########################################

if do_debug:
    cpu.show_inst = True
    cpu.show_mem_set = True
    cpu.show_mem_get = True

########################################
# run, starting at addr 0
########################################

if False and do_socket:
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
            in_channel_a.setup_telnet_linemode()
        elif keys == 'char':
            in_channel_a.setup_telnet_chars()
    imsai_devices.select_fd_on("stdin", sys.stdin, keyboard)

    while not do_run:
        imsai_devices.sleep_for_input(2)

    print("RUNNING")
    in_channel_a.clear()

try:
    cpu.reset(0)
    cpu.run()
finally:
    in_channel_a.done()
    out_channel_a.done()

# see IMSAI/basic4k.hex
# see IMSAI/basic4k.asm
# see IMSAI/basic4k.symbols

# see IMSAI/basic8k.hex
# see IMSAI/basic8k.asm

