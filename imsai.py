#!/usr/bin/python3

import sys
import intel8080
import imsai_devices

def go():
    interactive_out = True
    do_debug = False
    do_run_file = None
    do_4k = False
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
            do_4k = True
        elif arg == "-8":
            do_4k = False
        elif arg.lower().endswith('.bas'):
            do_run_file = arg

    cpu = intel8080.CPU8080(16*1024)

    ########################################
    # setup devices
    ########################################

    status_device = imsai_devices.StatusDevice()

    if interactive_out:
        out_device = imsai_devices.InteractiveOutputDevice("TTY", status_device)
    else:
        out_device = imsai_devices.TTYOutputDevice("TTY")

    if do_run_file:
        in_device = imsai_devices.ScriptedInputDevice("TTY", status_device, out_device)
        in_device.load_file(do_run_file)
        cpu.limit_steps = 5000000
    else:
        in_device = imsai_devices.InteractiveInputDevice("TTY", cpu, status_device)

    cpu.add_input_device(3, status_device)
    cpu.add_input_device(2, in_device)
    cpu.add_output_device(2, out_device)

    ########################################
    # load program BASIC
    ########################################

    if do_4k:
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
        status_device.add_tight_loop_addr(0x0086, 2996)
        status_device.add_tight_loop_addr(cpu.addr_to_number('TESTI'), 3)
    else:
        print("USING 8K BASIC")
        cpu.read_hex('IMSAI/basic8k.hex', 'IMSAI/basic8k.asm')
        cpu.extend_symbol('BEGPR', 250)
        cpu.set_read_only_end('RAM')
        status_device.add_tight_loop_addr(cpu.addr_to_number('TREAD'), 3)

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
    cpu.run()

    ########################################
    # print final results
    ########################################

    out_device.done()
    in_device.done()

if __name__ == '__main__':
    go()

# see IMSAI/basic4k.hex
# see IMSAI/basic4k.asm
# see IMSAI/basic4k.symbols

# see IMSAI/basic8k.hex
# see IMSAI/basic8k.asm

