#!/usr/bin/python3

import os
import sys
import intel8080
import imsai_devices

interactive_out = True
do_debug = False
run_basic = None
hex_file = None
basic_4k = False
do_mem = 16
for arg in sys.argv[1:]:
    if arg == "-d":
        do_debug = True
    elif arg.startswith("-m="):
        do_mem = int(arg[3:])
        if not (0 < do_mem <= 64):
            print("invalid memory")
            sys.exit(1)
    elif arg == "-o":
        interactive_out = False
    elif arg == "-4":
        basic_4k = True
    elif arg == "-8":
        basic_4k = False
    elif arg.lower().endswith('.bas'):
        run_basic = arg
    elif arg.lower().endswith('.hex'):
        hex_file = arg

cpu = intel8080.CPU8080(16*1024)

########################################
# setup devices
########################################

do_socket = False
status_device = imsai_devices.StatusDevice()

if interactive_out:
    out_device = imsai_devices.InteractiveOutputDevice("TTY", status_device)
else:
    out_device = imsai_devices.TTYOutputDevice("TTY")

if run_basic:
    in_device = imsai_devices.ScriptedInputDevice("TTY", status_device, out_device)
    in_device.load_file(run_basic)
    cpu.limit_steps = 5000000
    if basic_4k:
        hex_file = 'IMSAI/basic4k.symbols'
    else:
        hex_file = 'IMSAI/basic8k.symbols'
else:
    in_device = imsai_devices.SocketTtyDevice("Socket TTY", status_device, 8008)
    out_device = in_device
    do_socket = True

cpu.add_input_device(3, status_device)
cpu.add_input_device(2, in_device)
cpu.add_output_device(2, out_device)

########################################
# load program BASIC
########################################

if hex_file:
    sym_file = hex_file[:-3] + '.symbols'
    asm_file = hex_file[:-3] + '.asm'
    if os.path.exists(asm_file):
        cpu.read_asm(asm_file)
    if os.path.exists(sym_file):
        cpu.read_symbols(sym_file)
    cpu.read_hex(hex_file)
elif basic_4k:
    print("USING 4K BASIC")
    cpu.read_symbols('IMSAI/basic4k.symbols')
    cpu.read_hex('IMSAI/basic4k.hex')
    cpu.extend_symbol('FACC', 4)
    cpu.extend_symbol('FTEMP', 10)
    cpu.extend_symbol('IMMED', 70)
    cpu.extend_symbol('IOBUF', 40)
    cpu.extend_symbol('IOBUF', -2)
    cpu.extend_symbol('RNDNU', 4)
    cpu.extend_symbol('BEGPR', -2)
else:
    print("USING 8K BASIC")
    cpu.read_asm('IMSAI/basic8k.asm')
    cpu.read_hex('IMSAI/basic8k.hex')
    cpu.extend_symbol('BEGPR', 250)
    cpu.set_read_only_end('RAM')

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

cpu.reset(0)
if do_socket:
    do_run = False
    def keyboard(name, fd):
        global do_run
        keys = fd.readline().rstrip()
        if keys == 'run':
            do_run = True
        elif keys == 'exit':
            sys.exit(0)
    imsai_devices.select_fd_on("stdin", sys.stdin, keyboard)
    while not do_run:
        imsai_devices.sleep_for_input(2)

    print("RUNNING")
    in_device.clear()
    cpu.run()

    while True:
        imsai_devices.sleep_for_input(2)
else:
    cpu.run()

########################################
# print final results
########################################

out_device.done()
in_device.done()


# see IMSAI/basic4k.hex
# see IMSAI/basic4k.asm
# see IMSAI/basic4k.symbols

# see IMSAI/basic8k.hex
# see IMSAI/basic8k.asm

