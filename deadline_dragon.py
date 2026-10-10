"""Deadline Dragon: a tiny animated desktop companion for your deadlines.

Run with: python deadline_dragon.py
Drag the dragon to move it, left-click to poke, right-click for the menu.
Deadlines accept a date, YYYY-MM-DD HH:MM:SS, YYYY-MM, or days from today.
The quest board also has a month calendar and date/time spinner controls.
"""

import calendar
import math
import sqlite3
import sys
import time
import tkinter as tk
from datetime import date, datetime, time as clock_time, timedelta
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
        day = date.today() + timedelta(days=int(value))
        return datetime.combine(day, clock_time(23, 59, 59))
    if len(value) == 7 and value[4] == "-":
        try:
            year, month = map(int, value.split("-"))
            day = calendar.monthrange(year, month)[1]
            return datetime(year, month, day, 23, 59, 59)
        except (ValueError, OverflowError):
            pass
    try:
        parsed = datetime.fromisoformat(value)
        if "T" not in value and " " not in value:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(hour=23, minute=59, second=59)
        except ValueError:
            pass
    raise ValueError("Choose a date, enter YYYY-MM-DD or YYYY-MM-DD HH:MM:SS, or enter days from today.")


def due_datetime(value):
    parsed = datetime.fromisoformat(value)
    if "T" not in value and " " not in value:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def seconds_left(due, now=None):
    return (due_datetime(due) - (now or datetime.now())).total_seconds()


def when_text(seconds):
    late = seconds < 0
    total = int(abs(seconds))
    days, total = divmod(total, 86400)
    hours, total = divmod(total, 3600)
    minutes, secs = divmod(total, 60)
    if days:
        value = f"{days}d {hours}h"
    elif hours:
        value = f"{hours}h {minutes}m"
    elif minutes:
        value = f"{minutes}m {secs}s"
    else:
        value = f"{secs}s"
    return f"overdue {value}" if late else f"in {value}"


def due_display(value):
    return due_datetime(value).strftime("%d %b %Y · %I:%M:%S %p")


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
        self.last_tick = self.started
        self.rage = 0.0
        self.target_rage = 0.0
        self.gaze_x = 0.0
        self.gaze_y = 0.0
        self.gaze_target_x = 0.0
        self.gaze_target_y = 0.0
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
        c.bind("<Motion>", self.track_cursor)
        c.bind("<Leave>", self.reset_gaze)
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

    def track_cursor(self, event):
        self.gaze_target_x = clamp((event.x - 190) / 54, -1, 1) * 5.0
        self.gaze_target_y = clamp((event.y - 132) / 42, -1, 1) * 3.5

    def reset_gaze(self, _event=None):
        self.gaze_target_x = 0.0
        self.gaze_target_y = 0.0

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
            rows.sort(key=lambda row: due_datetime(row[2]))
            _rid, title, due = rows[0]
            remaining = seconds_left(due)
            days = remaining / 86400
            self.urgency = 1 if remaining < 0 else clamp(1-days/21, 0, 1)
            self.mood = ("overdue" if remaining < 0 else "furious" if days <= 2 else "angry" if days <= 6
                         else "alert" if days <= 13 else "calm")
            self.label = f"{title[:27]}{'…' if len(title)>27 else ''}  ·  {when_text(remaining)}"
            stops = [(0,(65,188,139)),(.42,(234,196,72)),(.72,(255,137,65)),(1,(247,83,86))]
            for (u0,c0),(u1,c1) in zip(stops,stops[1:]):
                if self.urgency <= u1:
                    self.target_rgb = mix(c0,c1,(self.urgency-u0)/(u1-u0)); break
            else: self.target_rgb = stops[-1][1]
        self.target_scale = .77 + .25*self.urgency
        self.target_rage = {"sleeping": 0.0, "calm": .04, "alert": .18,
                            "angry": .48, "furious": .84,
                            "overdue": 1.0}[self.mood]
        if old != self.mood and time.time() > self.quip_until:
            self.say({"sleeping":"I'm all ears. And horns.","calm":"Plenty of time. Make it count.",
                      "alert":"The clock is starting to breathe fire.","angry":"This one is getting close!",
                      "furious":"Two days. We can do this.","overdue":"Okay. No panic. Let's tackle it."}[self.mood])

    def refresh_loop(self):
        self.refresh(); self.root.after(1000, self.refresh_loop)

    def tick(self):
        now = time.time()
        dt = clamp(now - self.last_tick, 0.0, .1)
        self.last_tick = now
        ease = 1 - math.exp(-11 * dt)
        self.scale += (self.target_scale - self.scale) * ease
        self.rgb = mix(self.rgb, self.target_rgb, ease)
        rage_goal = 1.0 if now < self.roar_until else self.target_rage
        self.rage += (rage_goal - self.rage) * (1 - math.exp(-8 * dt))
        gaze_ease = 1 - math.exp(-15 * dt)
        self.gaze_x += (self.gaze_target_x - self.gaze_x) * gaze_ease
        self.gaze_y += (self.gaze_target_y - self.gaze_y) * gaze_ease
        self.draw()
        self.root.after(33, self.tick)
    def draw(self):
        c = self.canvas
        c.delete("all")
        now = time.time()
        t = now - self.started
        s = self.scale
        rage = self.rage
        urgent = self.urgency
        angry = self.mood in ("angry", "furious", "overdue")
        fire = now < self.roar_until or rage > .78
        heat = clamp((urgent - .5) / .5, 0, 1) ** .72
        blue = color(mix((12, 166, 229), (220, 55, 70), heat))
        light_blue = color(mix((90, 222, 246), (255, 158, 165), heat))
        bone_blue = "#147eae"
        ink = "#08283d"
        belly = "#ff973e"
        belly_hi = "#ffd080"
        belly_shadow = "#bd573e"
        red = "#d93648"
        lw = max(2, round(3.1 * s))
        breath = math.sin(t * 2.15) * 2.5
        shake = math.sin(t * 31) * rage * 1.05
        cx, cy = 190 + shake, 242 + breath + math.sin(t * 27) * rage * .5

        def p(x, y):
            return cx + x * s, cy + y * s

        def coords(values):
            out = []
            for i in range(0, len(values), 2):
                out.extend(p(values[i], values[i + 1]))
            return out

        def oval(x1, y1, x2, y2, **kw):
            c.create_oval(*p(x1, y1), *p(x2, y2), **kw)

        def poly(*xy, **kw):
            c.create_polygon(*coords(xy), **kw)

        def line(*xy, **kw):
            c.create_line(*coords(xy), **kw)

        def mirrored_poly(side, points, **kw):
            values = []
            for x, y in points:
                values.extend((side * x, y))
            c.create_polygon(*coords(values), **kw)

        def wing_point(side, x, y, angle):
            # Keep the wings compact without changing their shoulder hinge.
            x, y = x * .92, y * .92
            a = math.radians(angle)
            co, si = math.cos(a), math.sin(a)
            xr, yr = x * co - y * si, x * si + y * co
            return side * (31 + xr), -49 + yr

        def wing_shape(side, points, angle, **kw):
            values = []
            for x, y in points:
                wx, wy = wing_point(side, x, y, angle)
                values.extend((wx, wy))
            c.create_polygon(*coords(values), **kw)

        def wing_bone(side, points, angle, width=5):
            values = []
            for x, y in points:
                wx, wy = wing_point(side, x, y, angle)
                values.extend((wx, wy))
            # One clean dark edge and one blue fill keep the supports readable.
            c.create_line(*coords(values), fill=ink, width=max(3, round((width+2)*s)),
                          capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True)
            c.create_line(*coords(values), fill=bone_blue, width=max(2, round(width*s)),
                          capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True)

        # Small contact shadow grounds the upright pose.
        oval(-69, 75, 69, 85, fill="#253548", outline="")

        # Bat wings: a single scalloped membrane, with the finger bones kept
        # inside its silhouette and joined cleanly at the wrist.
        wingbeat = math.sin(t * (2.1 + rage * 4.2)) * (3 + rage * 6)
        wing_outline = [(0, 4), (10, -24), (34, -50), (66, -73),
                        (102, -91), (128, -94), (122, -69),
                        (106, -77), (92, -43), (76, -55),
                        (59, -16), (43, -31), (26, 5)]
        for side in (-1, 1):
            wing_shape(side, wing_outline, wingbeat, fill="#b92f46",
                       outline=ink, width=lw, smooth=True, splinesteps=24,
                       joinstyle=tk.ROUND)
            # A restrained inset gives the membrane depth without spilling
            # beyond the scalloped edge or covering the blue structure.
            wing_shape(side, [(8,-3),(35,-46),(66,-68),(99,-85),
                              (112,-86),(101,-80),(87,-52),(74,-59),
                              (58,-25),(42,-36),(25,-1)], wingbeat,
                       fill="#e14a50", outline="", smooth=True, splinesteps=20)
            # Shoulder to wrist forms the leading arm; the long digits fan
            # from that wrist and meet the scallops instead of crossing them.
            wrist = (53, -62)
            wing_bone(side, [(0,3),(22,-30),wrist,(94,-86),(126,-93)], wingbeat, 7)
            wing_bone(side, [wrist,(121,-70)], wingbeat, 5)
            wing_bone(side, [wrist,(92,-43)], wingbeat, 5)
            wing_bone(side, [wrist,(59,-17)], wingbeat, 5)

        # A tapered, jointed snake tail follows a hip-driven wave.
        tail_sway = math.sin(t * (1.4 + rage * 4.2)) * (7 + rage * 11)
        anchors = [(-31,28),(-57,51),(-84,44+tail_sway*.35),
                   (-110,57+tail_sway),(-138,51+tail_sway),(-159,37+tail_sway)]
        centers=[]
        for seg in range(len(anchors)-1):
            p0,p1=anchors[max(0,seg-1)],anchors[seg]
            p2,p3=anchors[seg+1],anchors[min(len(anchors)-1,seg+2)]
            for j in range(8):
                u=j/8; u2=u*u; u3=u2*u
                x=.5*((2*p1[0])+(-p0[0]+p2[0])*u+(2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*u2+(-p0[0]+3*p1[0]-3*p2[0]+p3[0])*u3)
                y=.5*((2*p1[1])+(-p0[1]+p2[1])*u+(2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*u2+(-p0[1]+3*p1[1]-3*p2[1]+p3[1])*u3)
                centers.append((x,y))
        centers.append(anchors[-1])
        upper,lower,frame=[],[],[]
        for i,(x,y) in enumerate(centers):
            a,b=centers[max(0,i-1)],centers[min(len(centers)-1,i+1)]
            dx,dy=b[0]-a[0],b[1]-a[1]
            ln=max(.001,math.hypot(dx,dy)); tx,ty=dx/ln,dy/ln
            nx,ny=-ty,tx
            u=i/max(1,len(centers)-1); r=15*(1-u)+3*u
            upper.append((x+nx*r,y+ny*r)); lower.append((x-nx*r,y-ny*r))
            frame.append((tx,ty,nx,ny,r))
        ribbon=upper+list(reversed(lower))
        c.create_polygon(*coords([v for q in ribbon for v in q]),
                         fill=ink,outline=ink,smooth=True,splinesteps=18)
        inner=[]
        for i,(x,y) in enumerate(centers):
            tx,ty,nx,ny,r=frame[i]; rr=max(1,r-3)
            inner.append((x+nx*rr,y+ny*rr))
        inner.extend((x-frame[i][2]*max(1,frame[i][4]-3),
                      y-frame[i][3]*max(1,frame[i][4]-3))
                     for i,(x,y) in reversed(list(enumerate(centers))))
        c.create_polygon(*coords([v for q in inner for v in q]),
                         fill=blue,outline="",smooth=True,splinesteps=18)
        # Vertebrae and scale ridges follow the bend, instead of a plain curved tube.
        for i in range(3,len(centers)-5,5):
            x,y=centers[i]; tx,ty,nx,ny,r=frame[i]
            line(x-tx*r*.7,y-ty*r*.7,x+tx*r*.7,y+ty*r*.7,
                 fill="#0874ae",width=max(1,round(2*s)),capstyle=tk.ROUND)
            line(x+nx*(r-5)+tx*3,y+ny*(r-5)+ty*3,
                 x+nx*(r-5)-tx*3,y+ny*(r-5)-ty*3,
                 fill=light_blue,width=max(1,round(1.6*s)),capstyle=tk.ROUND)
        for i in range(7,len(centers)-5,8):
            x,y=centers[i]; tx,ty,nx,ny,r=frame[i]
            bx,by=x+nx*(r-1),y+ny*(r-1)
            poly(bx-tx*5,by-ty*5,x+nx*(r+11),y+ny*(r+11),
                 bx+tx*6,by+ty*6,fill=red,outline=ink,
                 width=max(1,round(1.7*s)),smooth=True)

        # Legs and broad clawed feet are planted behind the torso.
        step = math.sin(t*2.2)*1.2
        for side,lift in ((-1,max(0,step)),(1,max(0,-step))):
            mirrored_poly(side,[(22,30),(46,35),(48,63),(39,73),(19,65),(17,43)],
                          fill=ink,outline=ink,width=lw,smooth=True,splinesteps=16)
            mirrored_poly(side,[(25,32),(42,38),(44,61),(37,67),(23,61),(21,43)],
                          fill=blue,outline="",smooth=True,splinesteps=16)
            oval(side*34-22,57-lift,side*34+22,79-lift,
                 fill=blue,outline=ink,width=lw)
            for toe in (-12,0,12):
                oval(side*34+toe-6,66-lift,side*34+toe+6,77-lift,
                     fill=blue,outline=ink,width=max(1,round(1.2*s)))
                poly(side*34+toe-2,72-lift,side*34+toe+3,82-lift,
                     side*34+toe+7,72-lift,fill="#f5f0df",outline=ink,
                     width=max(1,round(1.2*s)))

        # Torso and neck silhouette with orange, individually shaded belly plates.
        oval(-46,-58,46,55,fill=blue,outline=ink,width=lw)
        oval(-24,-99,24,-45,fill=blue,outline=ink,width=lw)
        poly(-18,-91,-29,-70,-31,-34,-26,10,-18,45,
             18,45,26,10,31,-34,29,-70,18,-91,
             fill=belly,outline=ink,width=max(2,round(2.5*s)),
             smooth=True,splinesteps=18)
        for y,half in ((-68,22),(-48,28),(-27,30),(-5,29),(17,26),(36,20)):
            line(-half,y,-half*.55,y+3,0,y+4,half*.55,y+3,half,y,
                 fill=belly_shadow,width=max(2,round(2*s)),smooth=True,capstyle=tk.ROUND)
            line(-half*.58,y-2,0,y,half*.58,y-2,
                 fill=belly_hi,width=max(1,round(1.4*s)),smooth=True,capstyle=tk.ROUND)

        # Arms attach below each shoulder, with highlights contained inside
        # the outline so no bright paint appears to leak around the edges.
        for side in (-1,1):
            arm=[(27,-56),(34,-63),(44,-65),(54,-58),(61,-46),(64,-33),
                 (62,-20),(56,-8),(49,-4),(43,-8),(42,-18),(45,-28),
                 (39,-37),(33,-44)]
            mirrored_poly(side,arm,fill=blue,outline=ink,width=lw,
                          smooth=True,splinesteps=18)
            mirrored_poly(side,[(34,-55),(39,-59),(47,-56),(50,-50),
                                (45,-46),(37,-49)],fill=light_blue,outline="",
                          smooth=True,splinesteps=12)
            line(side*38,-44,side*45,-38,side*50,-29,
                 fill="#54c8e7",width=max(1,round(1.5*s)),capstyle=tk.ROUND,smooth=True)
            line(side*51,-19,side*54,-15,
                 fill="#0874ae",width=max(1,round(1.4*s)),capstyle=tk.ROUND)
            oval(side*50-11,-3,side*50+12,19,
                 fill=blue,outline=ink,width=lw)
            for finger in (-1,0,1):
                fx=side*50+finger*6
                line(fx,13,fx+side*2,20,
                     fill=blue,width=max(3,round(4.2*s)),capstyle=tk.ROUND,smooth=True)
                line(fx+side*2,19,fx+side*4,23,
                     fill="#f7f1dd",width=max(1,round(1.8*s)),capstyle=tk.ROUND)

        # A tidy pair of small frills frames the crown without crowding the arms.
        for side in (-1, 1):
            mirrored_poly(side, [(45,-111),(56,-119),(53,-105)],
                          fill=red, outline=ink, width=max(1,round(1.5*s)),
                          smooth=True)

        # Large front-facing head with horns that curl outward on their own sides.
        oval(-54,-146,54,-61,fill=blue,outline=ink,width=lw)
        for side in (-1,1):
            horn=[(side*29,-132),(side*42,-144),(side*48,-158),
                  (side*40,-170),(side*34,-156),(side*23,-143)]
            poly(*[v for q in horn for v in q],fill="#eeeae1",outline=ink,
                 width=lw,smooth=True,splinesteps=18)
            line(side*36,-148,side*40,-154,fill="#aeb3b5",
                 width=max(1,round(2*s)),smooth=True)
            line(side*39,-158,side*43,-163,fill="#aeb3b5",
                 width=max(1,round(2*s)),smooth=True)
        poly(-13,-136,-9,-158,0,-148,8,-163,17,-136,8,-130,-7,-130,
             fill="#ff743f",outline=ink,width=lw,smooth=True,splinesteps=16)
        # Cheek pads and tiny temple scales sit on top of the head silhouette.
        oval(-52,-107,-31,-80,fill=light_blue,outline=ink,width=max(1,round(1.5*s)))
        oval(31,-107,52,-80,fill=light_blue,outline=ink,width=max(1,round(1.5*s)))
        for side in (-1,1):
            for y in (-91,-81):
                oval(side*43-3,y,side*43+1,y+4,fill="#9af0f6",outline="")

        # Eyeballs stay aligned while pupils ease toward the mouse pointer.
        blink=t%4.7<.14
        for side in (-1,1):
            ex=side*21
            oval(ex-16,-128,ex+16,-94,fill="#fffdf3",outline=ink,width=lw)
            if blink:
                line(ex-13,-110,ex,-105,ex+13,-110,
                     fill=ink,width=max(2,round(3*s)),smooth=True)
            else:
                pupil_x=ex+self.gaze_x
                pupil_y=-111+self.gaze_y
                oval(pupil_x-9,pupil_y-12,pupil_x+8,pupil_y+11,
                     fill="#2cb99a" if rage<.65 else "#f4a342",outline="")
                oval(pupil_x-4,pupil_y-11,pupil_x+4,pupil_y+10,fill=ink,outline="")
                oval(pupil_x-5,pupil_y-9,pupil_x-1,pupil_y-4,fill="white",outline="")
                oval(pupil_x+2,pupil_y+5,pupil_x+5,pupil_y+8,fill="#d7ffff",outline="")
            brow_y=-136+rage*8
            line(ex-15,brow_y-(3 if not angry else 0),ex,brow_y-5,
                 ex+15,brow_y-(3 if not angry else 0),fill=ink,
                 width=max(2,round((2.5+rage*2)*s)),capstyle=tk.ROUND,smooth=True)

        # Muzzle, nostrils, grin and fangs; the roar opens into a shaded mouth.
        oval(-40,-99,40,-63,fill=light_blue,outline=ink,width=lw)
        oval(-27,-95,-4,-77,fill="#5bcbe8",outline="")
        oval(4,-95,27,-77,fill="#5bcbe8",outline="")
        oval(-22,-91,-13,-84,fill=ink,outline="")
        oval(13,-91,22,-84,fill=ink,outline="")
        if fire:
            oval(-20,-79,20,-58,fill="#47182c",outline=ink,width=max(1,round(1.5*s)))
            oval(-10,-64,11,-58,fill="#ed5365",outline="")
            for x in (-16,9):
                poly(x,-79,x+7,-78,x+5,-70,fill="#fffdf1",outline=ink,
                     width=max(1,round(1*s)))
        else:
            line(-28,-78,-16,-70,0,-74,16,-70,28,-78,
                 fill=ink,width=max(2,round(2.5*s)),smooth=True)
            for x in (-17,10):
                poly(x,-77,x+7,-76,x+5,-69,fill="#fffdf1",outline=ink,
                     width=max(1,round(1*s)))
        for side in (-1,1):
            oval(side*34-4,-83,side*34+1,-79,fill="#f39a9a",outline="")
            oval(side*40-2,-89,side*40+1,-86,fill="#fac0b3",outline="")

        # Deadline rage eases into stronger wingbeats, a hotter gaze and fire.
        flame_length=0
        if fire:
            flame_length=46+25*rage
            flick=math.sin(t*(13+rage*9))
            spread=1+flick*.07
            far=-77+flame_length
            # Frontal breath projects toward the viewer and widens in depth.
            poly(0,-77,-7,-82,-9,-69,-17*spread,-56,
                 -22*spread,-40,-16*spread,far-8,0,far+5,
                 16*spread,far-8,22*spread,-40,17*spread,-56,
                 9,-69,7,-82,fill="#d93643",outline=ink,
                 width=max(1,round(1.4*s)),smooth=True,splinesteps=18)
            poly(0,-75,-5,-69,-12,-54,-13,-43,
                 0,-77+flame_length*.76,13,-43,12,-54,5,-69,
                 fill="#ff982e",outline="",smooth=True,splinesteps=16)
            poly(0,-72,-4,-65,-7,-54,0,-77+flame_length*.48,
                 7,-54,4,-65,fill="#fff3ae",outline="",
                 smooth=True,splinesteps=14)

        next_particles=[]
        for x,y,age,velocity in self.particles[-16:]:
            age+=.045; y+=velocity*s
            x+=math.sin(t*5+age*9)*.45*s
            if age<1:
                r=max(1,(1-age)*3*s)
                c.create_oval(x-r,y-r,x+r,y+r,
                              fill="#ffd16b" if age<.55 else "#f07855",outline="")
                next_particles.append([x,y,age,velocity])
        self.particles=next_particles
        if fire and now>=self.next_ember_at and len(self.particles)<16:
            ex,ey=p(0,-77+flame_length)
            self.particles.append([ex,ey,0,1.2+rage*.6])
            self.next_ember_at=now+.12

        # Clear floating deadline card and urgency meter.
        text=self.quip if now<self.quip_until else self.label
        card_w=min(W-24,max(206,min(350,34+len(text)*6.2)))
        x1,y1=(W-card_w)/2,12; x2,y2=x1+card_w,54
        c.create_rectangle(x1+2,y1+3,x2+2,y2+3,fill="#11131f",outline="")
        c.create_rectangle(x1,y1,x2,y2,fill="#242638",
                           outline=color(mix(self.rgb,(255,255,255),.15)),width=1)
        c.create_oval(x1+12,y1+13,x1+26,y1+27,fill=color(self.rgb),outline="")
        c.create_text(x1+34,(y1+y2)/2,text=text,anchor="w",
                      fill="#f6f1e7",font=("Segoe UI",10,"bold"))
        mx1,my,mx2=x1+12,y2-5,x2-12
        c.create_line(mx1,my,mx2,my,fill="#414356",width=2)
        c.create_line(mx1,my,mx1+(mx2-mx1)*urgent,my,
                      fill=color(self.rgb),width=2)
    def slay(self,rid):
        self.store.done(rid); self.roar_until=time.time()+1.2; self.refresh()
        self.say(f"A mighty victory! {self.store.slain()} deadline{'s' if self.store.slain()!=1 else ''} conquered.",4)

    def open_manager(self):
        if getattr(self,"manager",None) is not None and self.manager.winfo_exists():
            self.manager.lift(); self.manager.focus_force(); return
        win=tk.Toplevel(self.root); self.manager=win
        win.title("Deadline Dragon · Quest board"); win.attributes("-topmost",True)
        win.configure(bg="#111827")
        win.resizable(False,False); win.geometry(f"+{max(0,self.root.winfo_x()-510)}+{max(0,self.root.winfo_y()-80)}")
        win.bind("<Escape>",lambda _e:win.destroy())
        style=ttk.Style(win)
        try: style.theme_use("clam")
        except tk.TclError: pass
        style.configure("Lair.TFrame",background="#111827")
        style.configure("Card.TFrame",background="#1c2638")
        style.configure("Lair.TLabel",background="#111827",foreground="#e8edf5",font=("Segoe UI",10))
        style.configure("Muted.TLabel",background="#111827",foreground="#93a4bd",font=("Segoe UI",9))
        style.configure("Title.TLabel",background="#111827",foreground="#f5f7fb",font=("Segoe UI",18,"bold"))
        style.configure("Accent.TButton",background="#25a9d0",foreground="#071522",font=("Segoe UI",9,"bold"),padding=(12,7),borderwidth=0)
        style.map("Accent.TButton",background=[("active","#5bd0e9"),("disabled","#475569")])
        style.configure("Quiet.TButton",background="#29364a",foreground="#e8edf5",font=("Segoe UI",9),padding=(11,7),borderwidth=0)
        style.map("Quiet.TButton",background=[("active","#3a4c66")])
        style.configure("Lair.Treeview",background="#1c2638",fieldbackground="#1c2638",foreground="#e8edf5",rowheight=30,borderwidth=0,font=("Segoe UI",9))
        style.configure("Lair.Treeview.Heading",background="#29364a",foreground="#c7d3e4",font=("Segoe UI",9,"bold"),padding=(7,8),relief="flat")
        style.map("Lair.Treeview",background=[("selected","#176b88")],foreground=[("selected","#ffffff")])
        frm=ttk.Frame(win,padding=20,style="Lair.TFrame"); frm.grid()
        ttk.Label(frm,text="QUEST BOARD",style="Title.TLabel").grid(row=0,column=0,columnspan=3,sticky="w")
        ttk.Label(frm,text="Small steps tame big deadlines.",style="Muted.TLabel").grid(row=1,column=0,columnspan=3,sticky="w",pady=(2,16))
        title_v,due_v=tk.StringVar(),tk.StringVar()
        ttk.Label(frm,text="QUEST NAME",style="Muted.TLabel").grid(row=2,column=0,sticky="w")
        ttk.Label(frm,text="DUE DATE & TIME",style="Muted.TLabel").grid(row=2,column=1,sticky="w",padx=(8,0))
        title_e=ttk.Entry(frm,textvariable=title_v,width=28); title_e.grid(row=3,column=0,sticky="ew",pady=(5,0))
        due_e=ttk.Entry(frm,textvariable=due_v,width=24); due_e.grid(row=3,column=1,padx=(8,0),sticky="ew",pady=(5,0))

        def open_picker():
            try:
                initial=parse_due(due_v.get()) if due_v.get().strip() else datetime.now()+timedelta(days=1)
            except ValueError:
                initial=datetime.now()+timedelta(days=1)
            picker=tk.Toplevel(win); picker.title("Choose date and time")
            picker.configure(bg="#111827"); picker.resizable(False,False)
            picker.transient(win); picker.attributes("-topmost",True)
            picker.bind("<Escape>",lambda _e:picker.destroy())
            year_v=tk.StringVar(value=str(initial.year)); month_v=tk.StringVar(value=str(initial.month))
            day_v=tk.StringVar(value=str(initial.day)); hour_v=tk.StringVar(value=f"{initial.hour:02d}")
            minute_v=tk.StringVar(value=f"{initial.minute:02d}"); second_v=tk.StringVar(value=f"{initial.second:02d}")
            box=ttk.Frame(picker,padding=18,style="Lair.TFrame"); box.grid()
            ttk.Label(box,text="CHOOSE YOUR DEADLINE",style="Title.TLabel").grid(row=0,column=0,columnspan=7,sticky="w",pady=(0,12))
            nav=ttk.Frame(box,style="Lair.TFrame"); nav.grid(row=1,column=0,columnspan=7,sticky="ew",pady=(0,6))
            month_label=ttk.Label(nav,text="",style="Lair.TLabel",font=("Segoe UI",12,"bold"))
            month_label.pack(side="left",expand=True)
            refreshing=[False]
            day_buttons=[]

            def refresh_calendar(*_args):
                if refreshing[0]: return
                try: year,month=int(year_v.get()),int(month_v.get())
                except ValueError: return
                if not 1<=month<=12 or not 1<=year<=9999: return
                refreshing[0]=True
                last_day=calendar.monthrange(year,month)[1]
                try: selected=clamp(int(day_v.get()),1,last_day)
                except ValueError: selected=1
                day_v.set(str(selected)); month_label.config(text=f"{calendar.month_name[month]}  {year}")
                weeks=calendar.Calendar(firstweekday=6).monthdayscalendar(year,month)
                weeks += [[0]*7 for _ in range(6-len(weeks))]
                for index,button in enumerate(day_buttons):
                    value=weeks[index//7][index%7]
                    button.configure(text=str(value) if value else "",
                                      state=tk.NORMAL if value else tk.DISABLED,
                                      style="Accent.TButton" if value==selected else "Quiet.TButton",
                                      command=(lambda d=value:select_day(d)) if value else (lambda:None))
                refreshing[0]=False

            def select_day(value):
                if value:
                    day_v.set(str(value)); refresh_calendar()

            def shift_month(delta):
                year,month=int(year_v.get()),int(month_v.get())
                month+=delta
                if month<1: year-=1; month=12
                elif month>12: year+=1; month=1
                year_v.set(str(clamp(year,1,9999))); month_v.set(str(month))
                refresh_calendar()

            ttk.Button(nav,text="‹",width=3,style="Quiet.TButton",command=lambda:shift_month(-1)).pack(side="left")
            ttk.Button(nav,text="›",width=3,style="Quiet.TButton",command=lambda:shift_month(1)).pack(side="right")
            for col,name in enumerate(("S","M","T","W","T","F","S")):
                ttk.Label(box,text=name,style="Muted.TLabel",anchor="center",width=4).grid(row=2,column=col,pady=(3,4))
            for row in range(6):
                for col in range(7):
                    button=ttk.Button(box,text="",width=4,style="Quiet.TButton")
                    button.grid(row=3+row,column=col,padx=1,pady=1,ipady=2)
                    day_buttons.append(button)
            spinrow=ttk.Frame(box,style="Lair.TFrame"); spinrow.grid(row=9,column=0,columnspan=7,sticky="ew",pady=(14,0))
            fields=(("MONTH",month_v,1,12,5),("DAY",day_v,1,31,4),("YEAR",year_v,1,9999,7),
                    ("HOUR",hour_v,0,23,4),("MIN",minute_v,0,59,4),("SEC",second_v,0,59,4))
            for col,(label,var,low,high,width) in enumerate(fields):
                group=ttk.Frame(spinrow,style="Lair.TFrame"); group.grid(row=0,column=col,padx=(0,6))
                ttk.Label(group,text=label,style="Muted.TLabel").pack(anchor="w")
                ttk.Spinbox(group,from_=low,to=high,wrap=True,width=width,textvariable=var,
                            command=refresh_calendar).pack(anchor="w",pady=(3,0))
            month_v.trace_add("write",refresh_calendar); year_v.trace_add("write",refresh_calendar)
            day_v.trace_add("write",refresh_calendar)
            actions=ttk.Frame(box,style="Lair.TFrame"); actions.grid(row=10,column=0,columnspan=7,sticky="e",pady=(16,0))

            def use_date():
                try:
                    selected=datetime(int(year_v.get()),int(month_v.get()),int(day_v.get()),
                                      int(hour_v.get()),int(minute_v.get()),int(second_v.get()))
                except ValueError:
                    messagebox.showwarning("Deadline Dragon","Choose a valid date and time.",parent=picker); return
                due_v.set(selected.isoformat(sep=" ",timespec="seconds")); picker.destroy()

            ttk.Button(actions,text="Cancel",style="Quiet.TButton",command=picker.destroy).pack(side="left",padx=(0,7))
            ttk.Button(actions,text="Use this date",style="Accent.TButton",command=use_date).pack(side="left")
            refresh_calendar(); picker.geometry(f"+{max(0,win.winfo_x()+80)}+{max(0,win.winfo_y()+60)}")

        tree=ttk.Treeview(frm,columns=("title","due","left"),show="headings",height=8,style="Lair.Treeview",selectmode="browse")
        for col,label,width in (("title","QUEST",210),("due","DUE DATE · TIME",190),("left","TIME LEFT",125)):
            tree.heading(col,text=label); tree.column(col,width=width,anchor="w")
        tree.grid(row=5,column=0,columnspan=3,pady=(14,12),sticky="ew")
        tree.tag_configure("late",foreground="#ff8c91")
        status=ttk.Label(frm,text="",style="Muted.TLabel"); status.grid(row=6,column=0,sticky="w")
        due_values={}

        def update_countdowns():
            if not win.winfo_exists(): return
            for rid,due in due_values.items():
                remaining=seconds_left(due)
                tree.set(str(rid),"left",when_text(remaining))
                tree.item(str(rid),tags=("late",) if remaining<0 else ())
            win.after(1000,update_countdowns)

        def reload():
            tree.delete(*tree.get_children())
            due_values.clear()
            rows=self.store.pending()
            rows.sort(key=lambda row: due_datetime(row[2]))
            for rid,title,due in rows:
                remaining=seconds_left(due)
                due_values[rid]=due
                tree.insert("","end",iid=str(rid),values=(title,due_display(due),when_text(remaining)),tags=("late",) if remaining<0 else ())
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
            self.store.add(title,due.isoformat(sep=" ",timespec="seconds")); title_v.set(""); due_v.set("")
            reload(); self.refresh(); self.say("Quest accepted. I'll keep watch."); title_e.focus_set()
        def slay():
            rid=selected()
            if rid is not None: self.slay(rid); reload()
        def remove():
            rid=selected()
            if rid is not None: self.store.delete(rid); reload(); self.refresh()
        ttk.Button(frm,text="📅  Pick date & time",command=open_picker,style="Quiet.TButton").grid(row=4,column=0,columnspan=3,sticky="w",pady=(8,0))
        ttk.Button(frm,text="＋  Add quest",command=add,style="Accent.TButton").grid(row=3,column=2,padx=(10,0),pady=(5,0),sticky="ew")
        buttons=ttk.Frame(frm,style="Lair.TFrame"); buttons.grid(row=6,column=1,columnspan=2,sticky="e")
        ttk.Button(buttons,text="✓  Mark conquered",command=slay,style="Accent.TButton").pack(side="left",padx=(0,7))
        ttk.Button(buttons,text="Delete",command=remove,style="Quiet.TButton").pack(side="left")
        title_e.bind("<Return>",add); due_e.bind("<Return>",add)
        reload(); title_e.focus_set(); update_countdowns()


def main():
    root=tk.Tk(); DragonApp(root); root.mainloop()


if __name__ == "__main__":
    main()
