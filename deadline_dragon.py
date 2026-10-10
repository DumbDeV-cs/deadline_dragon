"""Deadline Dragon: a tiny animated desktop companion for your deadlines.

Run with: python deadline_dragon.py
Drag the dragon to move it, left-click to poke, right-click for the menu.
Dates accept YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY, or a number of days.
"""

import math
import random
import sqlite3
import sys
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

DB_PATH = Path(__file__).with_name("deadline_dragon.db")
W, H = 390, 340
KEY = "#010203"
INK = "#171827"
CREAM = "#fff0c9"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def color(rgb):
    return "#%02x%02x%02x" % tuple(int(clamp(v, 0, 255)) for v in rgb)


def shade(rgb, amount):
    return mix(rgb, (0, 0, 0), 1 - amount)


def parse_due(value):
    value = value.strip()
    if value.isdigit():
        return date.today() + timedelta(days=int(value))
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("Use YYYY-MM-DD, DD-MM-YYYY, or a number of days from today.")


def days_left(due):
    return (date.fromisoformat(due) - date.today()).days


def when_text(days):
    if days < 0:
        return f"{abs(days)}d overdue"
    return ("today!" if days == 0 else "tomorrow" if days == 1 else f"in {days} days")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS deadlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            due TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, done_at TEXT)""")
        self.db.commit()

    def pending(self):
        return self.db.execute("SELECT id,title,due FROM deadlines WHERE done=0 ORDER BY due,id").fetchall()

    def add(self, title, due):
        self.db.execute("INSERT INTO deadlines(title,due,created_at) VALUES(?,?,?)",
                        (title, due, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def done(self, rid):
        self.db.execute("UPDATE deadlines SET done=1,done_at=? WHERE id=?",
                        (datetime.now().isoformat(timespec="seconds"), rid))
        self.db.commit()

    def delete(self, rid):
        self.db.execute("DELETE FROM deadlines WHERE id=?", (rid,))
        self.db.commit()

    def slain(self):
        return self.db.execute("SELECT COUNT(*) FROM deadlines WHERE done=1").fetchone()[0]


class DragonApp:
    def __init__(self, root):
        self.root, self.store = root, Store(DB_PATH)
        self.mood, self.urgency, self.label = "sleeping", 0.0, "No deadlines yet · right-click to add one"
        self.scale, self.target_scale = .82, .82
        self.rgb = (83, 190, 145)
        self.target_rgb = self.rgb
        self.started = time.time()
        self.roar_until = 0
        self.quip, self.quip_until = "", 0
        self.particles = []
        self._window()
        self._events()
        self.refresh()
        self.tick()
        self.refresh_loop()

    def _window(self):
        r = self.root
        r.title("Deadline Dragon")
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        bg = INK
        try:
            if sys.platform.startswith("win"):
                r.wm_attributes("-transparentcolor", KEY); bg = KEY
            elif sys.platform == "darwin":
                r.wm_attributes("-transparent", True); r.config(bg="systemTransparent"); bg = "systemTransparent"
            else:
                r.wm_attributes("-transparentcolor", KEY); bg = KEY
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(r, width=W, height=H, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry(f"{W}x{H}+{sw-W-32}+{sh-H-72}")

    def _events(self):
        c = self.canvas
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.drag)
        c.bind("<ButtonRelease-1>", self.release)
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="＋  Add / manage deadlines", command=self.open_manager)
        self.menu.add_command(label="✦  Poke the dragon", command=self.poke)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.root.destroy)
        for seq in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            c.bind(seq, self.popup)
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.root.bind("<space>", lambda _e: self.poke())

    def popup(self, e):
        try: self.menu.tk_popup(e.x_root, e.y_root)
        finally: self.menu.grab_release()

    def press(self, e):
        self.press_xy = (e.x_root, e.y_root)
        self.drag_off = (e.x_root-self.root.winfo_x(), e.y_root-self.root.winfo_y())
        self.moved = False

    def drag(self, e):
        if abs(e.x_root-self.press_xy[0])+abs(e.y_root-self.press_xy[1]) > 5:
            self.moved = True
        if self.moved:
            self.root.geometry(f"+{e.x_root-self.drag_off[0]}+{e.y_root-self.drag_off[1]}")

    def release(self, _e):
        if not self.moved: self.poke()

    def say(self, text, seconds=3.5):
        self.quip, self.quip_until = text, time.time()+seconds

    def poke(self):
        lines = {"sleeping":"A tiny snack? Try a deadline.", "calm":"I have my eye on you.",
                 "alert":"We should probably make a start.", "angry":"Less scrolling. More doing.",
                 "furious":"FOCUS. I believe in you. Mostly.", "overdue":"We can still recover this. Move!"}
        self.say(lines[self.mood], 3.5)
        self.roar_until = time.time()+.55

    def refresh(self):
        rows = self.store.pending()
        old = self.mood
        if not rows:
            self.mood, self.urgency = "sleeping", 0
            self.label, self.target_rgb = "No deadlines yet · right-click to add one", (83, 190, 145)
        else:
            _rid, title, due = rows[0]
            d = days_left(due)
            self.urgency = 1 if d < 0 else clamp(1-d/21, 0, 1)
            self.mood = ("overdue" if d < 0 else "furious" if d <= 2 else "angry" if d <= 6
                         else "alert" if d <= 13 else "calm")
            self.label = f"{title[:27]}{'…' if len(title)>27 else ''}  ·  {when_text(d)}"
            stops = [(0,(65,188,139)),(.42,(234,196,72)),(.72,(255,137,65)),(1,(247,83,86))]
            for (u0,c0),(u1,c1) in zip(stops,stops[1:]):
                if self.urgency <= u1:
                    self.target_rgb = mix(c0,c1,(self.urgency-u0)/(u1-u0)); break
            else: self.target_rgb = stops[-1][1]
        self.target_scale = .82 + .27*self.urgency
        if old != self.mood and time.time() > self.quip_until:
            self.say({"sleeping":"I'm all ears. And horns.","calm":"Plenty of time. Make it count.",
                      "alert":"The clock is starting to breathe fire.","angry":"This one is getting close!",
                      "furious":"Two days. We can do this.","overdue":"Okay. No panic. Let's tackle it."}[self.mood])

    def refresh_loop(self):
        self.refresh(); self.root.after(30000, self.refresh_loop)

    def tick(self):
        self.scale += (self.target_scale-self.scale)*.07
        self.rgb = mix(self.rgb,self.target_rgb,.07)
        self.draw()
        self.root.after(33, self.tick)

    def draw(self):
        c = self.canvas; c.delete("all")
        now = time.time(); t = now-self.started; s = self.scale
        urgent = self.urgency
        roaring = now < self.roar_until
        fire = roaring or self.mood in ("furious","overdue")
        angry = self.mood in ("angry","furious","overdue")
        rgb = self.rgb; body=color(rgb); dark=color(shade(rgb,.52)); mid=color(shade(rgb,.78))
        hi=color(mix(rgb,(255,255,255),.32)); accent="#ffd16a"
        bob=math.sin(t*2.25)*3.4*s; cx,cy=177,bob+190
        jitter=random.uniform(-2,2) if roaring else 0; cx+=jitter
        def p(x,y): return (cx+x*s,cy+y*s)
        def coords(values):
            out=[]
            for i in range(0,len(values),2): out.extend(p(values[i],values[i+1]))
            return out
        def oval(x1,y1,x2,y2,**kw): c.create_oval(*p(x1,y1),*p(x2,y2),**kw)
        def poly(*xy,**kw): c.create_polygon(*coords(xy),**kw)
        def line(*xy,**kw): c.create_line(*coords(xy),**kw)
        lw=max(1,int(2*s))

        # soft ground shadow and little ambient halo
        oval(-75,61,82,76,fill="#252637",outline="")
        halo=color(mix(rgb,(255,255,255),.82))
        oval(-77,-105,89,64,fill="",outline=halo,width=1)

        # wing membrane: segmented silhouette, animated from the shoulder
        flap=math.sin(t*(2.4+urgent*3.4))* (9+urgent*10)
        poly(-14,-31,-70,-88-flap,-62,-44-flap*.45,-38,-73-flap*.8,-27,-34,
             -5,-65-flap*.55,5,-32,fill=dark,outline=color(shade(rgb,.4)),width=lw,smooth=True)
        for pts in ((-16,-36,-60,-80-flap),(-25,-39,-50,-45-flap*.5),(-31,-39,-35,-68-flap*.75)):
            line(*pts,fill=mid,width=max(1,int(2*s)),smooth=True)

        # curled tail with a luminous arrow tip
        sway=math.sin(t*1.7)*8
        line(-48,20,-79,35+sway*.5,-105,27+sway,-120,42+sway,
             fill=body,width=max(5,int(17*s)),smooth=True,capstyle=tk.ROUND)
        line(-84,32+sway*.5,-108,29+sway,fill=hi,width=max(1,int(2*s)),smooth=True)
        poly(-118,34+sway,-141,26+sway,-134,44+sway,-146,55+sway,-117,49+sway,
             fill=accent,outline=dark,width=lw)

        # back silhouette and neck
        poly(-42,-18,-52,-38,-38,-33,-29,-52,-19,-34,-6,-52,1,-29,18,-45,23,-23,
             fill=dark,outline="")
        line(18,-28,37,-53,fill=body,width=max(5,int(30*s)),capstyle=tk.ROUND,smooth=True)

        # sturdy legs, claws and torso
        for bx in (-39,11):
            oval(bx,32,bx+34,61,fill=dark,outline=color(shade(rgb,.4)),width=lw)
            oval(bx+4,35,bx+29,55,fill=body,outline="")
            for dx in (4,14,24):
                poly(bx+dx,57,bx+dx+5,67,bx+dx+9,57,fill=CREAM,outline="")
        oval(-56,-43,47,48,fill=dark,outline=color(shade(rgb,.42)),width=lw)
        oval(-52,-46,42,41,fill=body,outline="")
        # belly plate and engraved curved bands
        oval(-18,-21,35,39,fill=hi,outline=color(shade(rgb,.78)),width=1)
        for i in range(4):
            y=-11+i*12
            line(-13,y,1,y+3,20,y,fill=color(mix(rgb,(255,255,255),.12)),width=max(1,int(1.5*s)),smooth=True)
        # shoulder scales catch the light
        for row in range(3):
            for col in range(4):
                x=-39+col*12+(row%2)*5; y=-23+row*11
                if x < -15 or y < 2:
                    poly(x,y,x+4,y+3,x+8,y,x+4,y+7,fill=mid,outline="")

        # head silhouette, ears and cheek
        oval(23,-91,80,-34,fill=dark,outline=color(shade(rgb,.4)),width=lw)
        oval(26,-94,77,-38,fill=body,outline="")
        poly(44,-82,55,-73,39,-69,fill=hi,outline="")
        oval(58,-77,111,-48,fill=dark,outline=color(shade(rgb,.4)),width=lw)
        oval(58,-80,106,-52,fill=body,outline="")
        # horn pair with shaded base
        poly(33,-86,27,-119,47,-91,fill=CREAM,outline=dark,width=lw)
        poly(52,-88,57,-121,69,-88,fill=CREAM,outline=dark,width=lw)
        line(34,-89,30,-109,fill="#fff9e8",width=max(1,int(2*s)))
        line(55,-91,59,-112,fill="#fff9e8",width=max(1,int(2*s)))
        # expressive eye, brow, shine
        blink=(t%4.8)<.13
        if blink:
            line(51,-69,64,-66,77,-70,fill=dark,width=max(2,int(4*s)),smooth=True)
        else:
            oval(49,-78,76,-55,fill="#fff8e8",outline=dark,width=lw)
            oval(61,-75,70,-57,fill="#e26445" if angry else "#4b6e85",outline="")
            oval(64,-73,68,-60,fill="#171827",outline="")
            oval(65,-72,67,-69,fill="white",outline="")
            line(47,-81,61,-76,75,-82,fill=dark,width=max(2,int((4 if angry else 2)*s)),smooth=True)
        # muzzle, nostrils, friendly or determined mouth
        oval(94,-70,99,-65,fill=dark,outline="")
        oval(101,-68,105,-64,fill=dark,outline="")
        if fire:
            poly(102,-54,75,-52,82,-39,102,-43,fill=dark,outline="")
            line(103,-51,80,-50,fill="#6e2636",width=max(2,int(3*s)))
        else:
            line(103,-54,90,-50,80,-52,fill=dark,width=max(1,int(2*s)),smooth=True)
        # cheek freckles
        for x,y in ((85,-62),(91,-59),(86,-56)):
            oval(x,y,x+2,y+2,fill=hi,outline="")

        # flame ribbons with stable, smooth oscillation
        if fire:
            power=.7+.3*math.sin(t*18)
            length=(48+28*urgent)*(1.35 if roaring else 1)*power
            for offset, k, col in ((0,1,"#f05b3f"),(1,.72,"#ffad45"),(2,.43,"#fff0a1")):
                top=-52-10*k
                poly(103,top,103+length*.36,top-7*k*math.sin(t*14+offset),
                     103+length,top+8*k*math.sin(t*11+offset),
                     103+length*.57,top+12*k,103,top+10*k,
                     fill=col,outline="")
            if random.random()<.55:
                self.particles.append([cx+(108+length)*s,cy-52*s,random.uniform(0,1),random.uniform(-.7,.5)])
        # ember and smoke particles, bounded to keep redraw inexpensive
        next_particles=[]
        for x,y,age,vy in self.particles[-22:]:
            age+=.055; y-=2.2*s; x+=math.sin(t*7+age*12)*.7*s
            if age<1:
                r=(1+3*(1-age))*s
                ovalp=(x-r,y-r,x+r,y+r)
                c.create_oval(*ovalp,fill="#ffb653" if age<.55 else "#f07855",outline="")
                next_particles.append([x,y,age,vy])
        self.particles=next_particles

        # alert aura rings pulse with urgency
        if urgent>.55:
            pulse=(math.sin(t*4)+1)/2
            oval(-79-pulse*3,-103-pulse*3,88+pulse*3,66+pulse*3,
                 fill="",outline=color(mix(rgb,(255,255,255),.65)),width=1)

        # compact floating status card
        text=self.quip if now<self.quip_until else self.label
        card_w=min(W-24,max(206, min(350, 34+len(text)*6.2)))
        x1=(W-card_w)/2; y1=12; x2=x1+card_w; y2=54
        c.create_rectangle(x1+2,y1+3,x2+2,y2+3,fill="#11131f",outline="")
        c.create_rectangle(x1,y1,x2,y2,fill="#242638",outline=color(mix(rgb,(255,255,255),.15)),width=1)
        c.create_oval(x1+12,y1+13,x1+26,y1+27,fill=body,outline="")
        c.create_text(x1+34,(y1+y2)/2,text=text,anchor="w",fill="#f6f1e7",
                      font=("Segoe UI",10,"bold"))
        # urgency meter
        mx1=x1+12; my=y2-5; mx2=x2-12
        c.create_line(mx1,my,mx2,my,fill="#414356",width=2)
        c.create_line(mx1,my,mx1+(mx2-mx1)*urgent,my,fill=color(rgb),width=2)

    def slay(self,rid):
        self.store.done(rid); self.roar_until=time.time()+1.2; self.refresh()
        self.say(f"A mighty victory! {self.store.slain()} deadline{'s' if self.store.slain()!=1 else ''} conquered.",4)

    def open_manager(self):
        if getattr(self,"manager",None) is not None and self.manager.winfo_exists():
            self.manager.lift(); self.manager.focus_force(); return
        win=tk.Toplevel(self.root); self.manager=win
        win.title("Deadline Dragon · Lair ledger"); win.attributes("-topmost",True)
        win.resizable(False,False); win.geometry(f"+{max(0,self.root.winfo_x()-470)}+{max(0,self.root.winfo_y()-80)}")
        win.bind("<Escape>",lambda _e:win.destroy())
        frm=ttk.Frame(win,padding=16); frm.grid()
        ttk.Label(frm,text="Your quests",font=("Segoe UI",14,"bold")).grid(row=0,column=0,columnspan=3,sticky="w",pady=(0,10))
        title_v,due_v=tk.StringVar(),tk.StringVar()
        ttk.Label(frm,text="Deadline").grid(row=1,column=0,sticky="w")
        ttk.Label(frm,text="Due date · YYYY-MM-DD or days").grid(row=1,column=1,sticky="w",padx=(8,0))
        title_e=ttk.Entry(frm,textvariable=title_v,width=26); title_e.grid(row=2,column=0)
        due_e=ttk.Entry(frm,textvariable=due_v,width=24); due_e.grid(row=2,column=1,padx=(8,0))
        tree=ttk.Treeview(frm,columns=("title","due","left"),show="headings",height=9)
        for col,label,width in (("title","Quest",210),("due","Due",100),("left","Time left",115)):
            tree.heading(col,text=label); tree.column(col,width=width,anchor="w")
        tree.grid(row=3,column=0,columnspan=3,pady=(12,7),sticky="ew")
        tree.tag_configure("late",foreground="#bf3d45")
        status=ttk.Label(frm,text=""); status.grid(row=4,column=0,sticky="w")
        def reload():
            tree.delete(*tree.get_children())
            for rid,title,due in self.store.pending():
                d=days_left(due)
                tree.insert("","end",iid=str(rid),values=(title,due,when_text(d)),tags=("late",) if d<0 else ())
            status.config(text=f"Victories: {self.store.slain()}")
        def selected():
            sel=tree.selection(); return int(sel[0]) if sel else None
        def add(_e=None):
            title=title_v.get().strip()
            if not title:
                messagebox.showwarning("Deadline Dragon","Give this quest a name.",parent=win); return
            try: due=parse_due(due_v.get())
            except ValueError as err:
                messagebox.showwarning("Deadline Dragon",str(err),parent=win); return
            self.store.add(title,due.isoformat()); title_v.set(""); due_v.set("")
            reload(); self.refresh(); self.say("Quest accepted. I'll keep watch."); title_e.focus_set()
        def slay():
            rid=selected()
            if rid is not None: self.slay(rid); reload()
        def remove():
            rid=selected()
            if rid is not None: self.store.delete(rid); reload(); self.refresh()
        ttk.Button(frm,text="Add quest",command=add).grid(row=2,column=2,padx=(8,0))
        buttons=ttk.Frame(frm); buttons.grid(row=4,column=1,columnspan=2,sticky="e")
        ttk.Button(buttons,text="Mark conquered",command=slay).pack(side="left",padx=(0,6))
        ttk.Button(buttons,text="Delete",command=remove).pack(side="left")
        title_e.bind("<Return>",add); due_e.bind("<Return>",add)
        reload(); title_e.focus_set()


def main():
    root=tk.Tk(); DragonApp(root); root.mainloop()


if __name__ == "__main__":
    main()
