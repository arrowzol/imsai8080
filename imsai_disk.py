import time
import abstract_io

SEC_SZ = 0x80

class DiskDevice:
    def __init__(self, image_files):
        self.disks = [None]*16
        disk_number = 1
        for image_file in image_files:
            self.disks[disk_number] = open(image_file, 'r+b')
            disk_number += 1
        self.state = 0
        self.cmd = [0]*4

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

    def put_OUT_op(self, device_id, c):
        if self.state == 0:
            if c == 0x00:
                self.execute_cmd()
            elif c == 0x10:
                self.state = 1
        elif self.state == 1:
            self.cmd_addr = c
            self.state = 2
        elif self.state == 2:
            self.cmd_addr += c * 0x100
            self.state = 0

    def execute_cmd(self):
        # command string [page 45 of 160, DSK-35]
        # command string [page 48 of 160, DSK-38]
        #   command number 0: not used
        #   command number 1: write sector
        #   command number 2: read sector
        #   command number 3: format track
        #   command number 4: verify sector CRC
        #   command number 5: write deleted data address mark
        #   command number 6: configuration check - test for existence of drives
        #   command number 7-11: same as 1-5, but different
        cmd_byte =  self.cpu.mem[self.cmd_addr]     # command byte (command number + drive select number)

        # b0-b3: error number
        # b4: 0x10: class 3 error, hardware failure
        # b5: 0x20: class 2 error, operator recoverable error
        # b6: 0x40: class 1 error, error in command string
        # b7: 0x80: error
        status =    self.cpu.mem[self.cmd_addr + 1] # status byte

        # b0 - track bit 8
        # b1 side 0/1
        # b2-b5: platter 0-15
        # b6-b7: sector length
        #   0: 128
        #   1: 256
        #   2: 512
        #   3: 1024
        fmt =       self.cpu.mem[self.cmd_addr + 2] # sector format
            
        # track
        # 1-26 for SD 128 byte sectors
        # 1-15 for SD 256 byte sectors
        # 1-26 for DD 256 byte sectors
        # 1-8 for DD 1024 byte sectors
        trk =       self.cpu.mem[self.cmd_addr + 3] # track
        sec =       self.cpu.mem[self.cmd_addr + 4] # sector
        addr_l =    self.cpu.mem[self.cmd_addr + 5] # addr_l
        addr_h =    self.cpu.mem[self.cmd_addr + 6] # addr_h
        addr = addr_l + addr_h * 0x100

        if not status:
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
                    sector = bytearray(SEC_SZ)
                    for i in range(SEC_SZ):
                        sector[i] = self.cpu.mem[addr + i]
                    fh.seek(SEC_SZ*((sec-1) + 26*trk))
                    fh.write(sector)
                    self.cpu.mem[self.cmd_addr+1] = 1
                else:
                    abstract_io.log("D-WR drive:%s fmt:x%02x trk:%2d sec:%2d addr:x%04x"%(
                        disk_name, fmt, trk, sec, addr), 1)
                    self.cpu.mem[self.cmd_addr+1] = 0x9F

            # disk sector read
            # pg "FIF - 7", "page 63 of 160"
            elif cmd == 0x2:
                if fh:
                    fh.seek(SEC_SZ*((sec-1) + 26*trk))
                    sector = fh.read(SEC_SZ)
                    if len(sector) != SEC_SZ:
                        sector = bytearray(SEC_SZ)
                    for i in range(SEC_SZ):
                        self.cpu.mem[addr + i] = sector[i]
                    self.cpu.mem[self.cmd_addr+1] = 1
                else:
                    abstract_io.log("D-RD drive:%s fmt:x%02x trk:%2d sec:%2d addr:x%04x"%(
                        disk_name, fmt, trk, sec, addr), 1)
                    self.cpu.mem[self.cmd_addr+1] = 2
            else:
                abstract_io.log("D cmd_byte:x%02x fmt:x%02x trk:%2d sec:%2d addr:x%04x"%(
                    cmd_byte, fmt, trk, sec, addr), 1)
                self.cpu.mem[self.cmd_addr+1] = 0xFF

