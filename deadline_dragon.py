"""Deadline Dragon: a tiny animated desktop companion for your deadlines.

Run with: python deadline_dragon.py
Drag the dragon to move it, left-click to poke, right-click for the menu.
Dates accept YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY, or a number of days.
"""

import math
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
        self.next_ember_at = 0.0
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
        c = self.canvas
        c.delete("all")
        now = time.time()
        t = now - self.started
        s = self.scale
        urgent = self.urgency
        roaring = now < self.roar_until
        angry = self.mood in ("angry", "furious", "overdue")
        fire = roaring or self.mood in ("furious", "overdue")
        rgb = self.rgb
        body = color(rgb)
        shadow = "#173849"
        ink = "#173849"
        belly = "#ffe8b5"
        belly_shadow = "#e5b96f"
        highlight = color(mix(rgb, (255, 255, 255), .46))
        bob = math.sin(t * 2.4) * 3.2
        cx, cy = 160, 246 + bob
        line_width = max(2, round(3.2 * s))

        def p(x, y):
            return cx + x * s, cy + y * s

        def coords(values):
            result = []
            for i in range(0, len(values), 2):
                result.extend(p(values[i], values[i + 1]))
            return result

        def oval(x1, y1, x2, y2, **kw):
            x1, y1 = p(x1, y1)
            x2, y2 = p(x2, y2)
            c.create_oval(x1, y1, x2, y2, **kw)

        def poly(*xy, **kw):
            c.create_polygon(*coords(xy), **kw)

        def line(*xy, **kw):
            c.create_line(*coords(xy), **kw)

        def wing(points, angle, fill):
            radians = math.radians(angle)
            cosine, sine = math.cos(radians), math.sin(radians)
            rotated = []
            for i in range(0, len(points), 2):
                x, y = points[i], points[i + 1]
                rotated.extend((-16 + x * cosine - y * sine,
                                -31 + x * sine + y * cosine))
            c.create_polygon(*coords(rotated), fill=fill, outline=ink,
                             width=line_width, smooth=True, splinesteps=18,
                             joinstyle=tk.ROUND)
            return rotated

        # A soft little shadow grounds the floating mascot.
        oval(-71, 66, 72, 78, fill="#293348", outline="")

        # Two rounded wings flap from the same shoulder in a slow, readable arc.
        flap = math.sin(t * (3.2 + urgent * 1.4))
        far_wing = [-4, 0, -12, -35, -48, -93, -42, -83,
                    -42, -112, -22, -94, -12, -120, 1, -94, 15, -72, 24, -42]
        near_wing = [-1, 0, -1, -30, 19, -70, 24, -48,
                     42, -67, 40, -42, 55, -54, 50, -27, 37, -9]
        wing(far_wing, -8 + flap * 15, "#568a91")
        # Wing ribs are animated with the membrane and end in soft rounded tips.
        for x1, y1, x2, y2 in ((-8, -35, -47, -91), (-12, -35, -41, -108),
                               (-10, -33, -20, -92), (-7, -31, 4, -92)):
            line(x1, y1, x2, y2 + flap * 5, fill="#9bd0bf",
                 width=max(1, round(1.8 * s)), capstyle=tk.ROUND, smooth=True)
        wing(near_wing, 7 - flap * 12, "#78b9a2")

        # Tail swishes in a gentle S curve; its heart-shaped spade has an outline.
        sway = math.sin(t * 2.1) * 7
        line(-39, 20, -69, 34 + sway * .35, -94, 30 + sway,
             -111, 45 + sway, fill=ink, width=max(7, round(18 * s)),
             capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True)
        line(-39, 19, -69, 33 + sway * .35, -94, 29 + sway,
             -111, 44 + sway, fill=body, width=max(4, round(12 * s)),
             capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True)
        poly(-112, 44 + sway, -127, 33 + sway, -138, 35 + sway,
             -136, 47 + sway, -143, 55 + sway, -128, 54 + sway,
             -117, 61 + sway, fill="#f3bd55", outline=ink, width=line_width,
             smooth=True, splinesteps=16)

        # Back spikes peek above the body like a soft cartoon crest.
        poly(-49, -13, -53, -36, -36, -28, -30, -53, -14, -34,
             -3, -56, 10, -34, 23, -48, 27, -20,
             fill="#3c806e", outline=ink, width=line_width,
             smooth=True, splinesteps=16, joinstyle=tk.ROUND)

        # Little boots and three rounded claws; limbs bounce with the idle loop.
        step = math.sin(t * 2.4) * 2.0
        for bx, lift in ((-35, max(0, step)), (10, max(0, -step))):
            oval(bx - 3, 31 - lift, bx + 30, 63 - lift,
                 fill=ink, outline=ink, width=line_width)
            oval(bx, 29 - lift, bx + 27, 57 - lift,
                 fill=body, outline=ink, width=line_width)
            for toe in (bx + 3, bx + 12, bx + 21):
                oval(toe, 49 - lift, toe + 8, 60 - lift,
                     fill=belly, outline=ink, width=max(1, round(1.5 * s)))

        # Compact pear-shaped body with a bright cream belly plate.
        body_points = [-48, -28, -51, -2, -43, 27, -22, 43,
                       7, 48, 35, 31, 46, 5, 40, -25,
                       15, -47, -20, -49]
        c.create_polygon(*coords(body_points), fill=body, outline=ink,
                         width=line_width, smooth=True, splinesteps=22,
                         joinstyle=tk.ROUND)
        oval(-22, -13, 30, 39, fill=belly, outline=ink,
             width=max(1, round(1.5 * s)))
        for y in (-3, 8, 19, 29):
            line(-17, y, 1, y + 4, 24, y,
                 fill=belly_shadow, width=max(1, round(1.6 * s)), smooth=True)

        # One chunky arm waves hello; the other rests on the belly.
        arm_wave = math.sin(t * 3.0) * 7
        line(-35, -15, -54, -2 + arm_wave, -58, -15 + arm_wave,
             fill=ink, width=max(7, round(15 * s)), capstyle=tk.ROUND,
             joinstyle=tk.ROUND, smooth=True)
        line(-35, -15, -54, -2 + arm_wave, -58, -15 + arm_wave,
             fill=body, width=max(4, round(10 * s)), capstyle=tk.ROUND,
             joinstyle=tk.ROUND, smooth=True)
        oval(-67, -24 + arm_wave, -51, -7 + arm_wave,
             fill=body, outline=ink, width=line_width)
        for claw in (-64, -59, -54):
            line(claw, -22 + arm_wave, claw - 2, -28 + arm_wave,
                 fill=belly, width=max(1, round(2 * s)), capstyle=tk.ROUND)
        line(22, 0, 40, 8, 34, 20,
             fill=ink, width=max(6, round(12 * s)), capstyle=tk.ROUND,
             smooth=True)
        line(22, 0, 40, 8, 34, 20,
             fill=body, width=max(3, round(8 * s)), capstyle=tk.ROUND,
             smooth=True)

        # Large round head: oversized cheeks and muzzle make the silhouette cute.
        head_y = -77 + bob * .12
        oval(-8, head_y - 58, 84, head_y + 24,
             fill=ink, outline=ink, width=line_width)
        oval(-5, head_y - 62, 80, head_y + 20,
             fill=body, outline=ink, width=line_width)
        # Ear frills and horns sit behind the face.
        poly(-2, head_y - 29, -27, head_y - 43, -18, head_y - 17,
             fill="#4d9b7d", outline=ink, width=line_width, smooth=True)
        poly(13, head_y - 54, 5, head_y - 88, 29, head_y - 60,
             fill=belly, outline=ink, width=line_width, smooth=True)
        poly(42, head_y - 58, 54, head_y - 94, 64, head_y - 54,
             fill=belly, outline=ink, width=line_width, smooth=True)
        line(14, head_y - 62, 12, head_y - 79,
             fill="#fff8df", width=max(1, round(2 * s)), capstyle=tk.ROUND)
        line(49, head_y - 64, 53, head_y - 82,
             fill="#fff8df", width=max(1, round(2 * s)), capstyle=tk.ROUND)

        # A giant expressive eye with a moving pupil and a clear highlight.
        eye_top, eye_bottom = head_y - 43, head_y - 3
        blink_phase = t % 4.7
        blink = blink_phase < .16 or (angry and blink_phase > 4.35)
        if blink:
            line(17, head_y - 22, 31, head_y - 17, 47, head_y - 23,
                 fill=ink, width=max(2, round(4 * s)), smooth=True)
        else:
            oval(11, eye_top, 52, eye_bottom, fill="#fffaf0",
                 outline=ink, width=line_width)
            look = 2 if angry else math.sin(t * .9) * 2
            oval(24 + look, head_y - 35, 43 + look, head_y - 7,
                 fill="#54a9c4" if not angry else "#e87953",
                 outline="", width=0)
            oval(31 + look, head_y - 33, 39 + look, head_y - 8,
                 fill=ink, outline="")
            oval(28 + look, head_y - 31, 34 + look, head_y - 24,
                 fill="white", outline="")
            oval(37 + look, head_y - 16, 40 + look, head_y - 13,
                 fill="#d8f5eb", outline="")
        if angry:
            line(8, head_y - 49, 27, head_y - 44, 51, head_y - 49,
                 fill=ink, width=max(2, round(5 * s)), smooth=True)
        else:
            line(10, head_y - 48, 28, head_y - 52, 45, head_y - 48,
                 fill=ink, width=max(1, round(2.4 * s)), smooth=True)

        # Rounded snout, nostrils, rosy cheek, and a tiny toothy grin.
        oval(48, head_y - 27, 111, head_y + 8,
             fill=ink, outline=ink, width=line_width)
        oval(51, head_y - 31, 109, head_y + 4,
             fill=highlight, outline=ink, width=line_width)
        oval(91, head_y - 16, 98, head_y - 10, fill=ink, outline="")
        oval(101, head_y - 15, 106, head_y - 10, fill=ink, outline="")
        oval(67, head_y - 4, 88, head_y + 5,
             fill="#c84e52", outline=ink, width=max(1, round(1.5 * s)))
        line(66, head_y - 4, 83, head_y - 1, 93, head_y - 7,
             fill=ink, width=max(1, round(2 * s)), smooth=True)
        poly(74, head_y - 4, 81, head_y - 4, 79, head_y + 2,
             fill="white", outline="")
        for x in (57, 63, 69):
            oval(x, head_y - 12, x + 2.5, head_y - 9,
                 fill="#f3b4a2", outline="")

        # Fire is a layered ribbon with a wobbling tip; no frame-to-frame flicker.
        if fire:
            flame_length = (52 + 22 * urgent) * (1.12 if roaring else 1)
            flicker = math.sin(t * 15)
            poly(103, head_y - 8,
                 103 + flame_length * .28, head_y - 17 - flicker * 3,
                 103 + flame_length, head_y - 5 + flicker * 5,
                 103 + flame_length * .55, head_y + 6,
                 fill="#ef6048", outline=ink, width=max(1, round(1.5 * s)),
                 smooth=True, splinesteps=14)
            poly(103, head_y - 7,
                 103 + flame_length * .27, head_y - 12 - flicker * 2,
                 103 + flame_length * .72, head_y - 3 + flicker * 2,
                 103 + flame_length * .48, head_y + 2,
                 fill="#ffbd4f", outline="", smooth=True)
            poly(103, head_y - 6,
                 103 + flame_length * .24, head_y - 9,
                 103 + flame_length * .45, head_y - 3,
                 103 + flame_length * .34, head_y,
                 fill="#fff2ad", outline="", smooth=True)

        # Small embers drift upward; their count stays fixed and inexpensive.
        next_particles = []
        for x, y, age, velocity in self.particles[-16:]:
            age += .045
            y -= velocity * s
            x += math.sin(t * 5 + age * 9) * .45 * s
            if age < 1:
                radius = max(1, (1 - age) * 3.5 * s)
                c.create_oval(x - radius, y - radius, x + radius, y + radius,
                              fill="#ffce69" if age < .55 else "#f07855", outline="")
                next_particles.append([x, y, age, velocity])
        self.particles = next_particles
        if fire and now >= self.next_ember_at and len(self.particles) < 16:
            self.particles.append([cx + (112 + flame_length) * s,
                                   cy + (head_y - 5) * s, 0,
                                   1.2 + (int(t * 25) % 4) * .22])
            self.next_ember_at = now + .12

        # Status card stays small and legible, like a game HUD above the mascot.
        text = self.quip if now < self.quip_until else self.label
        card_w = min(W - 24, max(206, min(350, 34 + len(text) * 6.2)))
        x1, y1 = (W - card_w) / 2, 12
        x2, y2 = x1 + card_w, 54
        c.create_rectangle(x1 + 2, y1 + 3, x2 + 2, y2 + 3,
                           fill="#11131f", outline="")
        c.create_rectangle(x1, y1, x2, y2, fill="#242638",
                           outline=color(mix(rgb, (255, 255, 255), .15)), width=1)
        c.create_oval(x1 + 12, y1 + 13, x1 + 26, y1 + 27, fill=body, outline="")
        c.create_text(x1 + 34, (y1 + y2) / 2, text=text, anchor="w",
                      fill="#f6f1e7", font=("Segoe UI", 10, "bold"))
        mx1, my, mx2 = x1 + 12, y2 - 5, x2 - 12
        c.create_line(mx1, my, mx2, my, fill="#414356", width=2)
        c.create_line(mx1, my, mx1 + (mx2 - mx1) * urgent, my,
                      fill=color(rgb), width=2)

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
