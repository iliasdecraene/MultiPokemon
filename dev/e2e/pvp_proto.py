# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
import sys, random
sys.path.insert(0, _os.path.join(_REPO, "client"))
from pyboy import PyBoy
from yellowlink.game import GameMemory, RomData, Mon, full_heal
from yellowlink import text, game
rom = RomData.from_file(ROM)

def mk(sp, lvl, hp, t1, t2, moves, nick, ot, otid, st=30):
    d = bytearray(44); d[0]=sp; d[1:3]=hp.to_bytes(2,"big"); d[3]=lvl; d[5]=t1; d[6]=t2
    for i,mv in enumerate(moves): d[8+i]=mv; d[29+i]=rom.move_pp(mv)
    d[12:14]=otid.to_bytes(2,"big"); d[27:29]=b"\xaa\xaa"; d[33]=lvl; d[34:36]=hp.to_bytes(2,"big")
    for i in range(4): d[36+i*2:38+i*2]=st.to_bytes(2,"big")
    return Mon(bytes(d), text.encode(ot), text.encode(nick))

class Side:
    def __init__(self, name, party, master):
        self.pb = PyBoy(ROM, window="null", sound_emulated=False)
        self.pb.set_emulation_speed(0)
        self.pb.load_state(open(_os.path.join(STATES, "room.state"),"rb"))
        self.g = GameMemory(self.pb.memory, rom); self.m = self.pb.memory
        self.name, self.master = name, master
        g = self.g; g.m[game.PARTY_COUNT]=len(party)
        for i,mn in enumerate(party): g.write_party_slot(i, mn)
        g.m[game.PARTY_SPECIES+len(party)]=0xFF
        self.exchange = 0; self.sent = {}; self.inbox = {}; self.peer = None
        self.active = False
        pb = self.pb
        pb.hook_register(0x0F, 0x5777, self.on_exchange_start, None)
        pb.hook_register(0, 0x211F, self.on_nybble, None)
        pb.hook_register(0, 0x2149, self.on_zero, None)
    def ret(self):
        rf = self.pb.register_file; sp = rf.SP
        rf.PC = self.m[sp] | (self.m[sp+1] << 8); rf.SP = sp + 2
    def on_exchange_start(self, _):
        if self.active: self.exchange += 1
    def on_nybble(self, _):
        if not self.active: return
        n = self.exchange
        if n not in self.sent:
            v = self.m[0xCC42] & 0x0F; self.sent[n] = v; self.peer.inbox[n] = v
        if n in self.inbox:
            self.m[0xCC3E] = self.inbox[n]
        self.ret()
    def on_zero(self, _):
        if self.active: self.ret()
    def start(self, opp_party, opp_name, rns):
        g, m = self.g, self.m
        g.heal_party()
        mons = [full_heal(x, rom) for x in opp_party]
        m[game.ENEMY_PARTY_COUNT] = len(mons)
        for i in range(7): m[game.ENEMY_PARTY_SPECIES+i] = mons[i].species if i < len(mons) else 0xFF
        for i,mn in enumerate(mons):
            g.write(game.ENEMY_MONS+i*44, mn.data); g.write(game.ENEMY_OT+i*11, mn.ot); g.write(game.ENEMY_NICKS+i*11, mn.nick)
        g.write(0xD886, text.encode(opp_name.upper(), 11))   # wLinkEnemyTrainerName
        g.write(0xD147, bytes(rns)); m[0xCCDE] = 0             # random list + index
        m[0xFFAA] = 2 if self.master else 1                    # hSerialConnectionStatus
        m[0xD12A] = 4                                          # wLinkState = BATTLING
        m[game.LONE_ATTACK_NO] = 0
        m[game.CUR_OPPONENT] = 200 + 0x19                     # OPP_RIVAL1
        self.active = True

A_party = [mk(0x54, 20, 50, 0x17, 0x17, [0x54, 0x62], "SPARKY", "ASH", 111, 30)]
B_party = [mk(0xA5, 18, 45, 0, 0, [0x21, 0x27], "RATTY", "BROCK", 222, 28), mk(0x24, 15, 40, 0, 2, [0x10], "PIDGE", "BROCK", 222, 25)]
A = Side("Ash", A_party, True); B = Side("Brock", B_party, False); A.peer, B.peer = B, A
rns = [random.randrange(0, 0xFD) for _ in range(10)]
A.start(B_party, "Brock", rns); B.start(A_party, "Ash", rns)
log = []
started=False
script = []   # (frame, button) scheduled for B
fainted_at = None
seen_alive = False
for f in range(40000):
    b_hp = B.m[0xD014]<<8|B.m[0xD015]
    if b_hp > 0: seen_alive = True
    if B.m[0xD056] and b_hp == 0 and fainted_at is None and seen_alive:
        fainted_at = f
        script = [(f+400, "down"), (f+420, "a"), (f+460, "a"), (f+500, "a")]
    for s in (A, B):
        if s is B and fainted_at is not None and f < fainted_at + 520:
            for (when, btn) in script:
                if f == when: s.pb.button_press(btn)
                if f == when + 3: s.pb.button_release(btn)
        else:
            if f % 12 == 0: s.pb.button_press("a")
            if f % 12 == 3: s.pb.button_release("a")
        s.pb.tick()
    a = (A.m[0xD056], A.m[0xD014]<<8|A.m[0xD015], A.m[0xCFE5]<<8|A.m[0xCFE6], A.m[0xCFE4])
    b = (B.m[0xD056], B.m[0xD014]<<8|B.m[0xD015], B.m[0xCFE5]<<8|B.m[0xCFE6], B.m[0xCFE4])
    key = (a, b)
    if not log or log[-1][1] != key:
        log.append((f, key))
    if A.m[0xD056] and B.m[0xD056]: started=True
    if started and A.m[0xD056] == 0 and B.m[0xD056] == 0: break
for f,(a,b) in log:
    sync = "OK " if (a[1]==b[2] and a[2]==b[1]) or a[0]==0 or b[0]==0 else "-- "
    print(f, sync, "A(inB,myHP,enHP,enSp)", a, " B", b)
print("exchanges", A.exchange, B.exchange, "result A", A.m[0xCF0B], "B", B.m[0xCF0B])
A.pb.screen.image.save("a_end.png"); B.pb.screen.image.save("b_end.png")
