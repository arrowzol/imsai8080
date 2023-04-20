import time
import abstract_io

SEC_SZ = 0x80

########################################
# I/O port by mem-map control
########################################

OUT_PORT_GO = 0xFD

########################################
# I/O by port control
########################################

# CPM3
# UCSD system-p

FIRST_PORT = 0x0A
LAST_PORT = 0x15

OUT_PORT_FMT = 0x0A
OUT_PORT_TRK = 0x0B
OUT_PORT_SEC = 0x0C
OUT_PORT_READ = 0x0D
OUT_PORT_ADDR_L = 0x0F
OUT_PORT_ADDR_H = 0x10

# x00 (unset) - read on OUT_PORT_READ
# x03 - cmd on PORT_CMD
PORT_MODE = 0x14

IN_PORT_STATUS = 0x0E

# x00 - reset
# x01 - ?
# x02
PORT_CMD = 0x15
PORT_X = 0x11 # always 0

########################################
# code
########################################

class DiskDevice:
    def __init__(self, device_factory, disk_type, image_files):
        self.disks = [None]*16
        disk_number = 1
        for image_file in image_files:
            self.disks[disk_number] = open(image_file, 'r+b')
            disk_number += 1
        self.state = 0
        self.cmd = [0]*4
        self.disk_type = disk_type

        # old "go" port
        device_factory.add_output_device(OUT_PORT_GO, self)

        # new ports
        for device_id in range(FIRST_PORT, LAST_PORT+1):
            device_factory.add_output_device(device_id, self)
        device_factory.add_input_device(0x0E, self)

        self.bytes_from_in_ports = [0]*(LAST_PORT-FIRST_PORT+1)
        self.new_status = 0

    def boot(self, cpu):
        self.cpu = cpu

        # pg 44, 3-4 System Initialization

        # read track 0, sector 1 into x0000
        fh = self.disks[1]
        fh.seek(0)
        sector = fh.read(SEC_SZ)
        if len(sector) != SEC_SZ:
            print("can't read boot sector")
            cpu.halt = True
        for i in range(SEC_SZ):
            cpu.mem[i] = sector[i]

        # IBM 3740 format
        #   77 tracks
        #   26 sectors per track
        #   128 bytes per sector

        # up to 16 command strings

    def get_IN_op(self, cpu, device_id):
        if device_id == IN_PORT_STATUS:
            return self.new_status
        abstract_io.log("D-STATUS %02x"%device_id, 7)
        return 0

    def put_OUT_op(self, device_id, value):
        if device_id == OUT_PORT_GO:
            if self.state == 0:
                if value == 0x00:
                    cmd_byte = self.cpu.mem[self.cmd_addr]
                    status = self.cpu.mem[self.cmd_addr + 1]
                    fmt = self.cpu.mem[self.cmd_addr + 2]
                    trk = self.cpu.mem[self.cmd_addr + 3]
                    sec = self.cpu.mem[self.cmd_addr + 4]
                    addr_l = self.cpu.mem[self.cmd_addr + 5]
                    addr_h = self.cpu.mem[self.cmd_addr + 6]
                    addr = addr_l + addr_h * 0x100
                    status = self.execute_cmd(cmd_byte, status, fmt, trk, sec, addr)
                    self.cpu.mem[self.cmd_addr+1] = status
                elif value == 0x10:
                    self.state = 1
            elif self.state == 1:
                self.cmd_addr = value
                self.state = 2
            elif self.state == 2:
                self.cmd_addr += value * 0x100
                self.state = 0
        elif FIRST_PORT <= device_id <= LAST_PORT:
            self.bytes_from_in_ports[device_id - FIRST_PORT] = value
            if device_id == OUT_PORT_READ:
                def cfg(x):
                    return self.bytes_from_in_ports[x-FIRST_PORT]
                cmd_byte = 0x21 # TODO: select drive
                status = 0
                fmt = cfg(OUT_PORT_FMT)
                trk = cfg(OUT_PORT_TRK)
                sec = cfg(OUT_PORT_SEC)
                addr = cfg(OUT_PORT_ADDR_L) + 0x100*cfg(OUT_PORT_ADDR_H)
                old_status = self.execute_cmd(cmd_byte, status, fmt, trk, sec, addr)
                abstract_io.log("X: " + repr(self.bytes_from_in_ports))
                if old_status == 1:
                    self.new_status = 0
                else:
                    self.new_status = 1

    def execute_cmd(self, cmd_byte, status, fmt, trk, sec, addr):
        """
        cmd_byte - command byte (command number + drive select number)
            doc:
                command string [page 45 of 160, DSK-35]
                command string [page 48 of 160, DSK-38]
            cmds:
                command number 0: not used
                command number 1: write sector
                command number 2: read sector
                command number 3: format track
                command number 4: verify sector CRC
                command number 5: write deleted data address mark
                command number 6: configuration check - test for existence of drives
                command number 7-11: same as 1-5, but different

        status - status byte
            b0-b3: error number
            b4: 0x10: class 3 error, hardware failure
            b5: 0x20: class 2 error, operator recoverable error
            b6: 0x40: class 1 error, error in command string
            b7: 0x80: error

        fmt - sector format
            b0 - track bit 8
            b1 side 0/1
            b2-b5: platter 0-15
            b6-b7: sector length
                0: 128
                1: 256
                2: 512
                3: 1024
            
        track
            1-26 for SD 128 byte sectors
            1-15 for SD 256 byte sectors
            1-26 for DD 256 byte sectors
            1-8 for DD 1024 byte sectors
        """

        if status:
            return status

        cmd = (cmd_byte >> 4) & 0x0F
        disk_number = cmd_byte & 0x0F

        # TODO: why?
        if disk_number >= 3:
            disk_number -= 1

        disk_name = chr(ord('A')-1+disk_number)
        fh = self.disks[disk_number]

        # command number is the upper nibble of cmd [page 45 of 160, DSK-35]
        # disk sector write
        if cmd == 0x1:
            if fh:
                abstract_io.log("D-WR drive:%s fmt:x%02x trk:%2d sec:%2d addr:x%04x"%(
                    disk_name, fmt, trk, sec, addr), 3)
                sector = bytearray(SEC_SZ)
                for i in range(SEC_SZ):
                    sector[i] = self.cpu.mem[addr + i]
                fh.seek(SEC_SZ*((sec-1) + 26*trk))
                fh.write(sector)
                return 1
            else:
                abstract_io.log("D-WR drive:%s fmt:x%02x trk:%2d sec:%2d addr:x%04x"%(
                    disk_name, fmt, trk, sec, addr), 1)
                time.sleep(2)
                return 0x9F

        # disk sector read
        # pg "FIF - 7", "page 63 of 160"
        elif cmd == 0x2:
            if fh and 1 <= sec <= 26 and 0 <= trk <= 1000:
                seek_addr = SEC_SZ*((sec-1) + 26*trk)
                abstract_io.log("D-RD drive:%s fmt:x%02x trk:%2d sec:%2d addr:x%04x"%(
                    disk_name, fmt, trk, sec, addr), 3)
                fh.seek(seek_addr)
                sector = fh.read(SEC_SZ)
                if len(sector) != SEC_SZ:
                    sector = bytearray(SEC_SZ)
                for i in range(SEC_SZ):
                    self.cpu.mem[addr + i] = sector[i]
                return 1
            else:
                time.sleep(2)
                abstract_io.log("D-RD drive:%s fmt:x%02x trk:%2d sec:%2d addr:x%04x"%(
                    disk_name, fmt, trk, sec, addr), 1)
                return 2
        else:
            abstract_io.log("D cmd:%d cmd_byte:x%02x fmt:x%02x trk:%2d sec:%2d addr:x%04x"%(
                cmd, cmd_byte, fmt, trk, sec, addr), 1)
            time.sleep(2)
            return 0xFF

