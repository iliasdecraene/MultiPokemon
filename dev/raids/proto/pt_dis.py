import struct, sys, capstone
ram=open(sys.argv[3] if len(sys.argv)>3 else "/tmp/yl-raid-proto/pt_ram.bin","rb").read()
B=0x02000000
md=capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
def dis(start, end):
    a=start
    while a<end:
        code=ram[a-B:end-B]
        last=a
        for ins in md.disasm(code, a):
            extra=""
            if ins.mnemonic.startswith("ldr") and "[pc" in ins.op_str:
                imm=int(ins.op_str.split("#")[-1].rstrip("]"),16) if "#" in ins.op_str else 0
                lit=((ins.address+4)&~3)+imm
                extra="  ; =%08X"%struct.unpack_from("<I",ram,lit-B)[0]
            print("%08X %-8s %s%s"%(ins.address,ins.mnemonic,ins.op_str,extra))
            last=ins.address+ins.size
        if last<=a: last=a+2
        a=last
        if a<end: print("%08X  .data"%a); a+=2
s=int(sys.argv[1],16); e=int(sys.argv[2],16)
dis(s,e)
