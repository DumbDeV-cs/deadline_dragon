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
        angry = self.mood in ("angry", "furious", "overdue")
        fire = now < self.roar_until or self.mood in ("furious", "overdue")
        # Keep the mascot's signature blue; urgency changes the HUD and effects.
        blue = color(mix((14, 164, 224), (19, 121, 207), urgent * .28))
        blue_light = color(mix((81, 216, 242), (80, 173, 236), urgent * .2))
        blue_dark = "#0874ae"
        outline = "#09283d"
        orange = "#ff963f"
        orange_light = "#ffc06a"
        red = "#e54a45"
        bob = math.sin(t * 2.2) * 2.5
        cx, cy = 190, 242 + bob
        line_w = max(2, round(3.2 * s))

        def p(x, y):
            return cx + x * s, cy + y * s

        def coords(values):
            result = []
            for i in range(0, len(values), 2):
                result.extend(p(values[i], values[i + 1]))
            return result

        def oval(x1, y1, x2, y2, **kw):
            c.create_oval(*p(x1, y1), *p(x2, y2), **kw)

        def poly(*xy, **kw):
            c.create_polygon(*coords(xy), **kw)

        def line(*xy, **kw):
            c.create_line(*coords(xy), **kw)

        def wing(side, flap):
            # Broad bat wing, mirrored on each side and hinged at the shoulder.
            points = [(0, 0), (9, -22), (37, -65), (70, -88),
                      (66, -55), (108, -66), (86, -39), (124, -32),
                      (91, -13), (64, -20), (38, -16), (20, 5)]
            angle = math.radians(side * flap)
            co, si = math.cos(angle), math.sin(angle)
            transformed = []
            for x, y in points:
                x *= side
                transformed.extend((side * 31 + x * co - y * si,
                                    -47 + x * si + y * co))
            c.create_polygon(*coords(transformed), fill="#d94249", outline=outline,
                             width=line_w, smooth=True, splinesteps=20,
                             joinstyle=tk.ROUND)
            # Colored membrane panels and ribs give the wings real structure.
            panels = ((side * 36, -49, side * 97, -112,
                       side * 76, -69, side * 39, -38),
                      (side * 42, -47, side * 126, -79,
                       side * 88, -51, side * 45, -32),
                      (side * 44, -43, side * 121, -43,
                       side * 89, -31, side * 53, -27))
            for panel in panels:
                poly(*panel, fill="#f05b52", outline="")
            for x, y in ((side * 96, -108), (side * 119, -75),
                         (side * 116, -42), (side * 90, -31)):
                line(side * 33, -45, x, y, fill="#09283d",
                     width=max(1, round(1.7 * s)), capstyle=tk.ROUND,
                     smooth=True)
            line(side * 37, -48, side * 64, -81, side * 94, -101,
                 fill="#ff9b72", width=max(1, round(1.5 * s)),
                 capstyle=tk.ROUND, smooth=True)

        # Ground shadow and a gentle aura only when the dragon is worked up.
        oval(-70, 75, 70, 84, fill="#27374a", outline="")
        flap = math.sin(t * (2.8 + urgent * 1.2)) * (7 + 4 * urgent)
        wing(-1, flap)
        wing(1, flap)

        # Long curling tail with a row of red dorsal spikes.
        sway = math.sin(t * 1.7) * 7
        line(-34, 32, -61, 49, -90, 43 + sway * .45,
             -117, 54 + sway, -141, 42 + sway,
             fill=outline, width=max(7, round(22 * s)), smooth=True,
             capstyle=tk.ROUND, joinstyle=tk.ROUND)
        line(-34, 32, -61, 49, -90, 43 + sway * .45,
             -117, 54 + sway, -141, 42 + sway,
             fill=blue, width=max(4, round(16 * s)), smooth=True,
             capstyle=tk.ROUND, joinstyle=tk.ROUND)
        for x, y in ((-57, 43), (-78, 42 + sway * .3),
                     (-99, 48 + sway * .6), (-119, 49 + sway)):
            poly(x - 7, y + 2, x, y - 12, x + 8, y + 4,
                 fill=red, outline=outline, width=max(1, round(1.5 * s)))
        poly(-139, 42 + sway, -154, 32 + sway, -165, 35 + sway,
             -160, 46 + sway, -166, 56 + sway, -150, 53 + sway,
             -139, 61 + sway, -132, 50 + sway,
             fill=blue, outline=outline, width=line_w, smooth=True,
             splinesteps=16)

        # Back spikes silhouette, then short sturdy legs and clawed feet.
        poly(-43, -31, -54, -49, -37, -47, -30, -68,
             -16, -52, 0, -70, 12, -49, 29, -64, 35, -36,
             fill=red, outline=outline, width=line_w,
             smooth=True, splinesteps=16)
        step = math.sin(t * 2.2) * 2
        for x, lift in ((-31, max(0, step)), (16, max(0, -step))):
            line(x, 34, x - 3, 64 - lift, fill=outline,
                 width=max(8, round(23 * s)), capstyle=tk.ROUND, smooth=True)
            line(x, 34, x - 3, 63 - lift, fill=blue,
                 width=max(5, round(17 * s)), capstyle=tk.ROUND, smooth=True)
            oval(x - 17, 56 - lift, x + 17, 78 - lift,
                 fill=blue, outline=outline, width=line_w)
            for toe in (-12, -2, 8):
                oval(x + toe, 65 - lift, x + toe + 12, 77 - lift,
                     fill=blue_light, outline=outline,
                     width=max(1, round(1.5 * s)))
                poly(x + toe + 3, 73 - lift, x + toe + 7, 81 - lift,
                     x + toe + 11, 73 - lift,
                     fill="#f5f1dd", outline=outline,
                     width=max(1, round(1.2 * s)))

        # Upright body, with the reference's bold orange plated belly.
        oval(-47, -58, 47, 55, fill=blue, outline=outline, width=line_w)
        poly(-24, -79, -32, -54, -30, -16, -25, 26,
             -16, 48, 16, 48, 25, 26, 30, -16, 32, -54, 24, -79,
             fill=orange, outline=outline, width=max(2, round(2.4 * s)),
             smooth=True, splinesteps=18)
        for y in (-57, -38, -18, 3, 24):
            line(-27, y, -14, y + 4, 0, y + 5, 14, y + 4, 27, y,
                 fill="#a84939", width=max(1, round(1.7 * s)),
                 smooth=True, capstyle=tk.ROUND)
            line(-18, y - 2, 0, y, 18, y - 2,
                 fill=orange_light, width=max(1, round(1.1 * s)),
                 smooth=True)

        # Arms curve down from the shoulders; hands open into three claws.
        for side in (-1, 1):
            arm = (side * 36, -43, side * 49, -20, side * 56, 3,
                   side * 50, 20)
            line(*arm, fill=outline, width=max(8, round(21 * s)),
                 capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True)
            line(*arm, fill=blue, width=max(5, round(15 * s)),
                 capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True)
            oval(side * 43 - 8, 10, side * 43 + 11, 30,
                 fill=blue, outline=outline, width=line_w)
            for j in range(3):
                claw_x = side * 43 + side * (j - 1) * 7
                line(claw_x, 25, claw_x + side * 3, 33,
                     fill="#f5f1dd", width=max(1, round(2 * s)),
                     capstyle=tk.ROUND)

        # Neck and large round head with two bright, expressive eyes.
        oval(-27, -105, 27, -53, fill=blue, outline=outline, width=line_w)
        oval(-54, -146, 54, -62, fill=blue, outline=outline, width=line_w)
        # Rounded cheek pads and temple scales.
        oval(-53, -100, -28, -73, fill=blue_light, outline=outline,
             width=max(1, round(1.8 * s)))
        oval(28, -100, 53, -73, fill=blue_light, outline=outline,
             width=max(1, round(1.8 * s)))
        for side in (-1, 1):
            for y in (-91, -81):
                oval(side * 43 - 3, y, side * 43 + 1, y + 4,
                     fill="#8ce5f1", outline="")

        # Curved horns with segmented highlights, plus a flame-shaped crest.
        for side in (-1, 1):
            horn_x = side * 37
            poly(horn_x - 12, -133, horn_x - 19, -157,
                 horn_x - 15, -170, horn_x - 5, -150,
                 horn_x + 11, -133,
                 fill="#eee9db", outline=outline, width=line_w,
                 smooth=True, splinesteps=16)
            line(horn_x - 13, -151, horn_x - 4, -147,
                 fill="#aaa99f", width=max(1, round(1.5 * s)), smooth=True)
            line(horn_x - 14, -160, horn_x - 6, -157,
                 fill="#aaa99f", width=max(1, round(1.5 * s)), smooth=True)
        poly(-10, -137, -7, -162, 7, -150, 15, -137,
             fill="#ff743f", outline=outline, width=line_w,
             smooth=True, splinesteps=14)
        # Small side spines add a bright red silhouette around the head.
        for side, y in ((-1, -116), (1, -117), (-1, -96), (1, -96)):
            x = side * (50 if y < -105 else 53)
            poly(x - 3, y - 7, x + side * 12, y - 1,
                 x - 2, y + 7, fill="#f05b43", outline=outline,
                 width=max(1, round(1.5 * s)), smooth=True)

        blink_phase = t % 4.8
        blink = blink_phase < .14
        look = 2.2 if angry else math.sin(t * .75) * 2
        if blink:
            for side in (-1, 1):
                line(side * 35, -111, side * 21, -106, side * 7, -111,
                     fill=outline, width=max(2, round(4 * s)), smooth=True)
        else:
            for side in (-1, 1):
                ex = side * 21
                oval(ex - 16, -128, ex + 16, -94,
                     fill="#fffdf3", outline=outline, width=line_w)
                oval(ex - 9 + look, -123, ex + 8 + look, -98,
                     fill="#27bd9a" if not angry else "#ffbd42", outline="")
                oval(ex - 4 + look, -121, ex + 4 + look, -99,
                     fill=outline, outline="")
                oval(ex - 5 + look, -120, ex - 1 + look, -115,
                     fill="white", outline="")
                oval(ex + 3 + look, -105, ex + 6 + look, -102,
                     fill="#d9ffff", outline="")
        # Eyebrows lift when calm and tilt inward when deadlines get close.
        for side in (-1, 1):
            if angry:
                line(side * 39, -135, side * 23, -132,
                     side * 7, -137, fill=outline,
                     width=max(2, round(4 * s)), smooth=True)
            else:
                line(side * 38, -133, side * 22, -138,
                     side * 7, -133, fill=outline,
                     width=max(2, round(3 * s)), smooth=True)

        # Wide muzzle, nostrils and a friendly smile with two tiny fangs.
        oval(-38, -101, 38, -66, fill=blue_light,
             outline=outline, width=line_w)
        oval(-26, -97, -4, -77, fill="#49c5e9", outline="")
        oval(4, -97, 26, -77, fill="#49c5e9", outline="")
        oval(-21, -91, -12, -84, fill=outline, outline="")
        oval(12, -91, 21, -84, fill=outline, outline="")
        line(-28, -78, -16, -71, 0, -75, 16, -71, 28, -78,
             fill=outline, width=max(2, round(2.4 * s)),
             smooth=True, capstyle=tk.ROUND)
        for x in (-19, 12):
            poly(x, -77, x + 7, -76, x + 5, -68,
                 fill="#fffdf3", outline=outline,
                 width=max(1, round(1.1 * s)))

        # Layered fire breath when furious, overdue, or freshly poked.
        flame_length = 0
        if fire:
            flame_length = 60 + 24 * urgent
            flick = math.sin(t * 14)
            poly(31, -80, 55, -92 - flick * 3,
                 31 + flame_length, -74 + flick * 4,
                 57, -61, 31, -69,
                 fill="#e84b3f", outline=outline,
                 width=max(1, round(1.6 * s)), smooth=True, splinesteps=16)
            poly(31, -78, 53, -85 - flick * 2,
                 31 + flame_length * .76, -73 + flick * 2,
                 54, -66, 31, -70,
                 fill="#ff9c35", outline="", smooth=True)
            poly(31, -76, 49, -81,
                 31 + flame_length * .49, -73,
                 50, -70, 31, -71,
                 fill="#fff1a5", outline="", smooth=True)

        # A few warm embers drift from the flame without random frame flicker.
        next_particles = []
        for x, y, age, velocity in self.particles[-16:]:
            age += .045
            y -= velocity * s
            x += math.sin(t * 5 + age * 9) * .45 * s
            if age < 1:
                radius = max(1, (1 - age) * 3 * s)
                c.create_oval(x - radius, y - radius, x + radius, y + radius,
                              fill="#ffd16b" if age < .55 else "#f07855", outline="")
                next_particles.append([x, y, age, velocity])
        self.particles = next_particles
        if fire and now >= self.next_ember_at and len(self.particles) < 16:
            x, y = p(31 + flame_length, -73)
            self.particles.append([x, y, 0, 1.25])
            self.next_ember_at = now + .12

        # The compact status card remains visually separate from the mascot.
        text = self.quip if now < self.quip_until else self.label
        card_w = min(W - 24, max(206, min(350, 34 + len(text) * 6.2)))
        x1, y1 = (W - card_w) / 2, 12
        x2, y2 = x1 + card_w, 54
        c.create_rectangle(x1 + 2, y1 + 3, x2 + 2, y2 + 3,
                           fill="#11131f", outline="")
        c.create_rectangle(x1, y1, x2, y2, fill="#242638",
                           outline=color(mix(self.rgb, (255, 255, 255), .15)), width=1)
        c.create_oval(x1 + 12, y1 + 13, x1 + 26, y1 + 27,
                      fill=color(self.rgb), outline="")
        c.create_text(x1 + 34, (y1 + y2) / 2, text=text, anchor="w",
                      fill="#f6f1e7", font=("Segoe UI", 10, "bold"))
        mx1, my, mx2 = x1 + 12, y2 - 5, x2 - 12
        c.create_line(mx1, my, mx2, my, fill="#414356", width=2)
        c.create_line(mx1, my, mx1 + (mx2 - mx1) * urgent, my,
                      fill=color(self.rgb), width=2)
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
