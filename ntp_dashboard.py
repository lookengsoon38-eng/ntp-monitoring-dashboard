
import tkinter as tk
from tkinter import filedialog, messagebox # basic Python GUI framework
import customtkinter as ctk                # Provides modern styled widgets for the dashboard UI
import socket                              # UDP communication with NTP servers
import struct                              # For packing/unpacking NTP packet data
import time                                # For measuring latency and scheduling            
import csv                                 # For logging results to CSV
import threading                           # For running network queries without freezing the UI       
import datetime                            # For handling and displaying time data
import os                                  # For file operations (checking if log exists, etc.) 
from collections import deque              # Used for fixed-size graph history storage                

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec

# Appearance
ctk.set_default_color_theme("blue")

# Palette 
C = {
    "bg":          "#0D1117",
    "panel":       "#161B22",
    "border":      "#21262D",
    "accent":      "#58A6FF",
    "accent2":     "#3FB950",
    "warn":        "#F0883E",
    "danger":      "#F85149",
    "text":        "#E6EDF3",
    "text_muted":  "#8B949E",
    "card":        "#1C2128",
    "card_hover":  "#252C36",
    "graph_bg":    "#0D1117",
    "graph_grid":  "#1E252E",
    "quality_ex":  "#3FB950",
    "quality_gd":  "#58A6FF",
    "quality_fa":  "#F0883E",
    "quality_po":  "#F85149",
}

# Colors cycled for comparison mode bars
CMP_COLORS = [
    "#58A6FF", "#3FB950", "#F0883E", "#D2A8FF",
    "#79C0FF", "#56D364", "#FFA657", "#FF7B72",
]

# These servers are shown in the dropdown list
NTP_SERVERS_DEFAULT = [
    "pool.ntp.org",
    "time.google.com",
    "time.cloudflare.com",
    "time.windows.com",
    "ntp.ubuntu.com",
    "0.pool.ntp.org",
    "1.pool.ntp.org",
    "asia.pool.ntp.org",
]

LOG_FILE   = "ntp_log.csv"  # CSV file where results are logged when "Download CSV" button is clicked
MAX_POINTS = 60             # Maximum graph history points
NTP_PORT   = 123            # Standard NTP port
NTP_DELTA  = 2208988800     # Difference between NTP and Unix epoch

# LATENCY QUALITY THRESHOLDS (in milliseconds)
QUALITY_EXCELLENT = 30
QUALITY_GOOD      = 80
QUALITY_FAIR      = 200


# NTP core query function 
def query_ntp(server: str, timeout: float = 3.0):
    packet = b"\x1b" + 47 * b"\0"                                   # Build standard 48-byte NTP request packet
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:     # Create UDP socket connection
        s.settimeout(timeout)                                       # Set timeout to avoid hanging if server doesn't respond
        t_send = time.time()                                        # Record time immediately before sending request for latency calculation    
        s.sendto(packet, (server, NTP_PORT))                        # Send the NTP request packet to the specified server and port        
        data, _ = s.recvfrom(1024)                                  # Wait for response (up to 1024 bytes, though NTP responses are typically 48 bytes)
        t_recv = time.time()                                        # Record time immediately after receiving response for latency calculation
    if len(data) < 48:                                              # Validate response length (NTP responses should be at least 48 bytes); if too short, raise an error
        raise ValueError("Short NTP response")
    unpacked = struct.unpack("!12I", data)                          # Unpack the first 48 bytes of the response into 12 unsigned integers (NTP timestamp fields are in the last 8 bytes)
    tx_time  = unpacked[10] + unpacked[11] / 2**32 - NTP_DELTA      # Calculate the server's transmit time from the NTP response (convert from NTP epoch to Unix epoch)
    latency_ms = (t_recv - t_send) * 1000                           # Calculate round-trip latency in milliseconds (time from sending request to receiving response)
    drift_ms   = (tx_time - (t_send + t_recv) / 2) * 1000           # Calculate clock drift in milliseconds 
    server_dt  = datetime.datetime.fromtimestamp(tx_time)           # Convert server transmit time to a human-readable datetime object for display in the UI
    return server_dt, latency_ms, drift_ms

# CSV LOGGING FUNCTION
def log_csv(server, status, latency, drift):
    write_header = not os.path.exists(LOG_FILE)                     # Check if CSV file exists
    with open(LOG_FILE, "a", newline="") as f:                      # Open CSV file in append mode (creates file if it doesn't exist)
        w = csv.writer(f)                                   
        if write_header:                                            # If file is new, write header row with column names
            w.writerow(["timestamp", "server", "status", "latency_ms", "drift_ms"])
        w.writerow([                                                # Write monitoring data row
            datetime.datetime.now().isoformat(timespec="seconds"),
            server, status,
            f"{latency:.2f}" if latency is not None else "",
            f"{drift:.2f}"   if drift   is not None else "",
        ])

# LATENCY QUALITY EVALUATION
def quality_label(ms):
    if ms < QUALITY_EXCELLENT: return "EXCELLENT", C["quality_ex"]
    if ms < QUALITY_GOOD:      return "GOOD",      C["quality_gd"]
    if ms < QUALITY_FAIR:      return "FAIR",      C["quality_fa"]
    return "POOR", C["quality_po"]


#  MAIN APPLICATION CLASS
class NTPDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Network Time Monitoring Dashboard")     # Set window title
        self.geometry("1300x860")                           # Set initial window size (width x height)
        self.minsize(1080, 740)                             # Set minimum window size to ensure UI elements fit properly
        self.configure(fg_color=C["bg"])                    # Set background color of the main window using the defined color palette    
       
        self.server_list = list(NTP_SERVERS_DEFAULT)        # Server list (mutable – user can add to it)

        # State variables for UI elements and monitoring data
        self.server_var  = ctk.StringVar(value=self.server_list[0])
        self.auto_var    = ctk.BooleanVar(value=True)
        self.status_var  = ctk.StringVar(value="IDLE")
        self.time_var    = ctk.StringVar(value="--:--:--")
        self.latency_var = ctk.StringVar(value="— ms")
        self.drift_var   = ctk.StringVar(value="— ms")
        self.ip_var      = ctk.StringVar(value="—")
        self.min_var     = ctk.StringVar(value="—")
        self.max_var     = ctk.StringVar(value="—")
        self.avg_var     = ctk.StringVar(value="—")
        self.quality_var = ctk.StringVar(value="—")

        self.poll_count = 0
        self.fail_count = 0
        self._all_latencies: list[float] = []

        self.latency_history = deque([0.0] * MAX_POINTS, maxlen=MAX_POINTS)
        self.drift_history   = deque([0.0] * MAX_POINTS, maxlen=MAX_POINTS)
        self._auto_job  = None
        self._querying  = False

        # Comparison mode state
        self._cmp_running   = False
        self._cmp_results   = {}   # server -> {"latency", "drift", "status"}
        self._cmp_window    = None

        self._build_ui()
        self._build_graph()
        self._tick_clock()
        self.after(300, self._do_query)

    # UI BUILD
    def _build_ui(self):
        # Header 
        header = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0, height=56)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="      CLIENT NETWORK TIME PROTOCOL MONITORING DASHBOARD",
            font=ctk.CTkFont(family="Tahoma", size=18, weight="bold"),
            text_color=C["text"]
        ).pack(side="left")

        self._status_badge = ctk.CTkLabel(
            header, textvariable=self.status_var,
            font=ctk.CTkFont(family="Courier New", size=11, weight="bold"),
            text_color=C["bg"], fg_color=C["text_muted"],
            corner_radius=6, width=100, height=26
        )
        self._status_badge.pack(side="right", padx=20)

        self._local_clock = ctk.CTkLabel(
            header, text="",
            font=ctk.CTkFont(family="Courier New", size=13),
            text_color=C["text_muted"]
        )
        self._local_clock.pack(side="right", padx=(0, 16))

        # Body 
        body = ctk.CTkFrame(self, fg_color=C["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=(12, 16))

        left = ctk.CTkFrame(body, fg_color=C["bg"], width=310)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)

        right = ctk.CTkFrame(body, fg_color=C["bg"])
        right.pack(side="left", fill="both", expand=True)

        self._build_controls(left)

        # Scrollable area so all stat cards are reachable on smaller screens
        self._stat_scroll = ctk.CTkScrollableFrame(
            left, fg_color="transparent",
            scrollbar_button_color=C["border"],
            scrollbar_button_hover_color=C["text_muted"]
        )
        self._stat_scroll.pack(fill="both", expand=True)

        self._build_stat_cards(self._stat_scroll)
        self._graph_frame = right

    # Controls panel 
    def _build_controls(self, parent):
        card = self._card(parent, "SERVER CONFIGURATION")
        card.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(card, text="NTP Server", font=self._label_font(),
                     text_color=C["text_muted"]).pack(anchor="w", padx=16, pady=(10, 2))

        self._server_menu = ctk.CTkComboBox(
            card, values=self.server_list, variable=self.server_var,
            font=ctk.CTkFont(family="Courier New", size=12),
            fg_color=C["card"], border_color=C["border"],
            button_color=C["accent"], button_hover_color="#3A8EE6",
            dropdown_fg_color=C["panel"], dropdown_hover_color=C["card_hover"],
            text_color=C["text"], corner_radius=6, height=34,
            command=self._change_server
        )
        self._server_menu.pack(fill="x", padx=16, pady=(0, 8))

        # ADD CUSTOM SERVER row 
        add_frame = ctk.CTkFrame(card, fg_color=C["card"], corner_radius=6,
                                 border_width=1, border_color=C["border"])
        add_frame.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(add_frame, text="Add server",
                     font=ctk.CTkFont(family="Courier New", size=10),
                     text_color=C["text_muted"]).pack(anchor="w", padx=10, pady=(6, 0))

        entry_row = ctk.CTkFrame(add_frame, fg_color="transparent")
        entry_row.pack(fill="x", padx=8, pady=(2, 8))

        self._custom_entry = ctk.CTkEntry(
            entry_row, placeholder_text="e.g. ntp.example.com",
            font=ctk.CTkFont(family="Courier New", size=12),
            fg_color=C["bg"], border_color=C["border"],
            text_color=C["text"], corner_radius=5, height=30
        )
        self._custom_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._custom_entry.bind("<Return>", lambda _: self._add_custom_server())

        ctk.CTkButton(
            entry_row, text="+", width=30, height=30,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=C["accent"], hover_color="#3A8EE6",
            text_color=C["bg"], corner_radius=5,
            command=self._add_custom_server
        ).pack(side="left")

        self._add_status_lbl = ctk.CTkLabel(
            add_frame, text="",
            font=ctk.CTkFont(family="Courier New", size=10),
            text_color=C["accent2"]
        )
        self._add_status_lbl.pack(anchor="w", padx=10, pady=(0, 4))

        # IP & quality 
        ip_row = ctk.CTkFrame(card, fg_color="transparent")
        ip_row.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(ip_row, text="IP  :", font=self._label_font(),
                     text_color=C["text_muted"]).pack(side="left")
        ctk.CTkLabel(ip_row, textvariable=self.ip_var,
                     font=ctk.CTkFont(family="Courier New", size=12),
                     text_color=C["accent"]).pack(side="left", padx=8)

        qi_row = ctk.CTkFrame(card, fg_color="transparent")
        qi_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(qi_row, text="Quality  :", font=self._label_font(),
                     text_color=C["text_muted"]).pack(side="left")
        self._quality_badge = ctk.CTkLabel(
            qi_row, textvariable=self.quality_var,
            font=ctk.CTkFont(family="Courier New", size=11, weight="bold"),
            text_color=C["bg"], fg_color=C["text_muted"],
            corner_radius=5, width=80, height=22
        )
        self._quality_badge.pack(side="left", padx=8)

        # Button grid
        br1 = ctk.CTkFrame(card, fg_color="transparent")
        br1.pack(fill="x", padx=16, pady=(0, 6))

        self._query_btn = ctk.CTkButton(
            br1, text="▶  QUERY",
            font=ctk.CTkFont(family="Courier New", size=12, weight="bold"),
            fg_color=C["accent"], hover_color="#3A8EE6",
            text_color=C["bg"], corner_radius=6, height=34,
            command=self._do_query
        )
        self._query_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            br1, text="⬇ CSV",
            font=ctk.CTkFont(family="Courier New", size=12),
            fg_color=C["card"], hover_color=C["card_hover"],
            text_color=C["text_muted"], border_color=C["border"],
            border_width=1, corner_radius=6, height=34,
            command=self._open_csv
        ).pack(side="left")

        br2 = ctk.CTkFrame(card, fg_color="transparent")
        br2.pack(fill="x", padx=16, pady=(0, 6))

        ctk.CTkButton(
            br2, text="🖼 Save Graph",
            font=ctk.CTkFont(family="Courier New", size=12),
            fg_color=C["card"], hover_color=C["card_hover"],
            text_color=C["accent"], border_color=C["accent"],
            border_width=1, corner_radius=6, height=34,
            command=self._save_graph
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            br2, text="↺ Reset",
            font=ctk.CTkFont(family="Courier New", size=12),
            fg_color=C["card"], hover_color=C["card_hover"],
            text_color=C["text_muted"], border_color=C["border"],
            border_width=1, corner_radius=6, height=34,
            command=self._reset_stats
        ).pack(side="left")

        # COMPARISON MODE button 
        br3 = ctk.CTkFrame(card, fg_color="transparent")
        br3.pack(fill="x", padx=16, pady=(0, 12))

        self._cmp_btn = ctk.CTkButton(
            br3, text=" Compare Servers",
            font=ctk.CTkFont(family="Courier New", size=12, weight="bold"),
            fg_color="#2D333B", hover_color="#373E47",
            text_color=C["warn"], border_color=C["warn"],
            border_width=1, corner_radius=6, height=34,
            command=self._open_comparison_window
        )
        self._cmp_btn.pack(fill="x")

        # Auto-refresh 
        auto_row = ctk.CTkFrame(card, fg_color="transparent")
        auto_row.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(auto_row, text="Auto-refresh (5 s)",
                     font=self._label_font(), text_color=C["text_muted"]).pack(side="left")
        ctk.CTkSwitch(
            auto_row, text="", variable=self.auto_var,
            onvalue=True, offvalue=False,
            progress_color=C["accent2"], button_color=C["text"],
            command=self._toggle_auto
        ).pack(side="right")

    # Stat cards panel
    def _build_stat_cards(self, parent):
        self._time_card = self._metric_card(
            parent, "NTP SERVER TIME", self.time_var, icon="🕐", accent=C["accent"])
        self._time_card.pack(fill="x", pady=(0, 8))

        row1 = ctk.CTkFrame(parent, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 8))
        self._lat_card = self._metric_card(
            row1, "LATENCY", self.latency_var, icon="📡", accent=C["accent2"])
        self._lat_card.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self._drift_card = self._metric_card(
            row1, "CLOCK DRIFT", self.drift_var, icon="⏱", accent=C["warn"])
        self._drift_card.pack(side="left", fill="both", expand=True)

        poll_card = self._card(parent, "SESSION STATISTICS")
        poll_card.pack(fill="x", pady=(0, 8))
        sg = ctk.CTkFrame(poll_card, fg_color="transparent")
        sg.pack(fill="x", padx=16, pady=(6, 14))
        self._poll_lbl = self._stat_row(sg, "Polls sent",   "0",     C["text"])
        self._fail_lbl = self._stat_row(sg, "Failures",     "0",     C["danger"])
        self._loss_lbl = self._stat_row(sg, "Packet loss",  "0.0 %", C["warn"])

        row2 = ctk.CTkFrame(parent, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 8))
        self._min_card = self._metric_card(
            row2, "MIN", self.min_var, icon="↓", accent=C["accent2"])
        self._min_card.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self._avg_card = self._metric_card(
            row2, "AVG", self.avg_var, icon="~", accent=C["accent"])
        self._avg_card.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self._max_card = self._metric_card(
            row2, "MAX", self.max_var, icon="↑", accent=C["warn"])
        self._max_card.pack(side="left", fill="both", expand=True)

    # GRAPH BUILDING
    def _build_graph(self):
        card = ctk.CTkFrame(self._graph_frame, fg_color=C["panel"],
                            corner_radius=10, border_width=1, border_color=C["border"])
        card.pack(fill="both", expand=True)

        title_bar = ctk.CTkFrame(card, fg_color="transparent", height=40)
        title_bar.pack(fill="x", padx=16, pady=(10, 0))
        title_bar.pack_propagate(False)
        ctk.CTkLabel(
            title_bar, text="LIVE LATENCY  &  CLOCK DRIFT",
            font=ctk.CTkFont(family="Courier New", size=11, weight="bold"),
            text_color=C["text_muted"]
        ).pack(side="left", anchor="w")
        ctk.CTkLabel(
            title_bar, text=f"Last {MAX_POINTS} samples",
            font=self._label_font(), text_color=C["border"]
        ).pack(side="right", anchor="e")

        self.fig = plt.Figure(figsize=(7, 4.8), dpi=100, facecolor=C["graph_bg"])
        gs = gridspec.GridSpec(2, 1, figure=self.fig, hspace=0.55,
                               top=0.93, bottom=0.08, left=0.09, right=0.96)
        self.ax_lat   = self.fig.add_subplot(gs[0])
        self.ax_drift = self.fig.add_subplot(gs[1])

        for ax in (self.ax_lat, self.ax_drift):
            ax.set_facecolor(C["graph_bg"])
            ax.tick_params(colors=C["text_muted"], labelsize=8)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            for sp in ("left", "bottom"):
                ax.spines[sp].set_color(C["border"])
            ax.grid(True, color=C["graph_grid"], linewidth=0.6, linestyle="--", alpha=0.8)

        xs = list(range(MAX_POINTS))
        self.ax_lat.set_title("Latency (ms)", color=C["accent2"], fontsize=9,
                               fontfamily="monospace", pad=4, loc="left")
        self.ax_lat.fill_between(xs, list(self.latency_history), alpha=0.18, color=C["accent2"])
        self.lat_line, = self.ax_lat.plot(xs, list(self.latency_history),
                                           color=C["accent2"], linewidth=1.8)
        self.lat_dot,  = self.ax_lat.plot([MAX_POINTS-1], [0], "o",
                                           color=C["accent2"], markersize=6, zorder=5)
        for val, col, lbl in [
            (QUALITY_EXCELLENT, C["quality_ex"], "Excellent"),
            (QUALITY_GOOD,      C["quality_gd"], "Good"),
            (QUALITY_FAIR,      C["quality_fa"], "Fair"),
        ]:
            self.ax_lat.axhline(val, color=col, linewidth=0.7, linestyle=":", alpha=0.6)
            self.ax_lat.text(1, val+1, lbl, color=col, fontsize=6,
                              fontfamily="monospace",
                              transform=self.ax_lat.get_yaxis_transform(), va="bottom")

        self.ax_drift.set_title("Clock Drift (ms)", color=C["warn"], fontsize=9,
                                 fontfamily="monospace", pad=4, loc="left")
        self.ax_drift.fill_between(xs, list(self.drift_history), alpha=0.18, color=C["warn"])
        self.drft_line, = self.ax_drift.plot(xs, list(self.drift_history),
                                              color=C["warn"], linewidth=1.8)
        self.drft_dot,  = self.ax_drift.plot([MAX_POINTS-1], [0], "o",
                                              color=C["warn"], markersize=6, zorder=5)
        self.ax_drift.axhline(0, color=C["border"], linewidth=0.8, linestyle=":")

        self.canvas = FigureCanvasTkAgg(self.fig, master=card)
        self.canvas.get_tk_widget().configure(bg=C["graph_bg"], highlightthickness=0)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(4, 12))
        self.canvas.draw()

    # ADD CUSTOM SERVER
    def _add_custom_server(self):
        hostname = self._custom_entry.get().strip().lower()
        if not hostname:
            return
        if hostname in self.server_list:
            self._set_add_status("Already in list", C["warn"])
            return

        # Validate: try DNS resolution in background so UI doesn't freeze
        self._set_add_status("Checking…", C["text_muted"])
        threading.Thread(target=self._validate_and_add, args=(hostname,), daemon=True).start()

    def _validate_and_add(self, hostname):
        try:
            socket.gethostbyname(hostname)
            valid = True
            err   = ""
        except Exception as e:
            valid = False
            err   = str(e)
        self.after(0, self._finish_add, hostname, valid, err)

    def _finish_add(self, hostname, valid, err):
        if valid:
            self.server_list.append(hostname)
            self._server_menu.configure(values=self.server_list)
            self._server_menu.set(hostname)
            self.server_var.set(hostname)
            self._custom_entry.delete(0, "end")
            self._set_add_status(f"✓ Added: {hostname}", C["accent2"])
            self._change_server(hostname)
        else:
            self._set_add_status(f"✗ Invalid host", C["danger"])

    def _set_add_status(self, msg, color):
        self._add_status_lbl.configure(text=msg, text_color=color)
        # Clear message after 3 s
        self.after(3000, lambda: self._add_status_lbl.configure(text=""))


    # SERVER COMPARISON MODE
    def _open_comparison_window(self):
        if self._cmp_window and self._cmp_window.winfo_exists():
            self._cmp_window.lift()
            return

        win = ctk.CTkToplevel(self)
        win.title("Server Comparison Mode")
        win.geometry("900x680")
        win.minsize(760, 560)
        win.configure(fg_color=C["bg"])
        self._cmp_window = win

        # Header 
        hdr = ctk.CTkFrame(win, fg_color=C["panel"], corner_radius=0, height=50)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="⚖  Server Comparison Mode",
                     font=ctk.CTkFont(family="Courier New", size=13, weight="bold"),
                     text_color=C["warn"]).pack(side="left", padx=20)

        self._cmp_status_lbl = ctk.CTkLabel(
            hdr, text="Select servers and run",
            font=ctk.CTkFont(family="Courier New", size=11),
            text_color=C["text_muted"]
        )
        self._cmp_status_lbl.pack(side="right", padx=20)

        body = ctk.CTkFrame(win, fg_color=C["bg"])
        body.pack(fill="both", expand=True, padx=14, pady=12)

        # Left: server selector 
        left = ctk.CTkFrame(body, fg_color=C["panel"], corner_radius=10,
                            border_width=1, border_color=C["border"], width=240)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)

        ctk.CTkLabel(left, text="SELECT SERVERS",
                     font=ctk.CTkFont(family="Courier New", size=10, weight="bold"),
                     text_color=C["text_muted"]).pack(anchor="w", padx=14, pady=(12, 6))

        scroll = ctk.CTkScrollableFrame(left, fg_color="transparent",
                                         scrollbar_button_color=C["border"])
        scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._cmp_checks  = {}   # server -> BooleanVar
        self._cmp_check_widgets = {}

        def _make_check(srv):
            var = ctk.BooleanVar(value=True)
            cb  = ctk.CTkCheckBox(
                scroll, text=srv, variable=var,
                font=ctk.CTkFont(family="Courier New", size=11),
                text_color=C["text"], fg_color=C["accent"],
                hover_color="#3A8EE6", border_color=C["border"],
                corner_radius=4, height=28
            )
            cb.pack(anchor="w", pady=2)
            self._cmp_checks[srv]         = var
            self._cmp_check_widgets[srv]  = cb

        for srv in self.server_list:
            _make_check(srv)

        # Button to refresh list if user added new servers
        ctk.CTkButton(
            left, text="↺ Refresh list",
            font=ctk.CTkFont(family="Courier New", size=11),
            fg_color=C["card"], hover_color=C["card_hover"],
            text_color=C["text_muted"], border_color=C["border"],
            border_width=1, corner_radius=6, height=28,
            command=lambda: self._cmp_refresh_list(scroll)
        ).pack(fill="x", padx=10, pady=(0, 6))

        ctk.CTkButton(
            left, text="▶  Run Comparison",
            font=ctk.CTkFont(family="Courier New", size=12, weight="bold"),
            fg_color=C["warn"], hover_color="#D4761F",
            text_color=C["bg"], corner_radius=6, height=36,
            command=self._run_comparison
        ).pack(fill="x", padx=10, pady=(0, 12))

        # Right: results 
        right = ctk.CTkFrame(body, fg_color=C["bg"])
        right.pack(side="left", fill="both", expand=True)

        # Bar chart
        chart_card = ctk.CTkFrame(right, fg_color=C["panel"], corner_radius=10,
                                   border_width=1, border_color=C["border"])
        chart_card.pack(fill="both", expand=True, pady=(0, 10))

        chart_hdr = ctk.CTkFrame(chart_card, fg_color="transparent", height=36)
        chart_hdr.pack(fill="x", padx=14, pady=(10, 0))
        chart_hdr.pack_propagate(False)
        ctk.CTkLabel(chart_hdr, text="LATENCY COMPARISON (ms)",
                     font=ctk.CTkFont(family="Courier New", size=10, weight="bold"),
                     text_color=C["text_muted"]).pack(side="left")
        self._cmp_save_btn = ctk.CTkButton(
            chart_hdr, text="🖼 Save", width=70, height=26,
            font=ctk.CTkFont(family="Courier New", size=11),
            fg_color=C["card"], hover_color=C["card_hover"],
            text_color=C["accent"], border_color=C["accent"],
            border_width=1, corner_radius=5,
            command=self._save_comparison_chart
        )
        self._cmp_save_btn.pack(side="right")

        self._cmp_fig = plt.Figure(figsize=(5, 2.8), dpi=100, facecolor=C["graph_bg"])
        self._cmp_ax  = self._cmp_fig.add_subplot(111)
        self._cmp_ax.set_facecolor(C["graph_bg"])
        self._cmp_ax.tick_params(colors=C["text_muted"], labelsize=8)
        self._cmp_ax.spines["top"].set_visible(False)
        self._cmp_ax.spines["right"].set_visible(False)
        for sp in ("left", "bottom"):
            self._cmp_ax.spines[sp].set_color(C["border"])
        self._cmp_ax.grid(True, color=C["graph_grid"], linewidth=0.5, linestyle="--", alpha=0.7, axis="y")
        self._cmp_ax.set_xlabel("Server", color=C["text_muted"], fontsize=8)
        self._cmp_ax.set_ylabel("Latency (ms)", color=C["text_muted"], fontsize=8)
        self._cmp_fig.subplots_adjust(left=0.12, right=0.97, top=0.92, bottom=0.22)

        self._cmp_canvas = FigureCanvasTkAgg(self._cmp_fig, master=chart_card)
        self._cmp_canvas.get_tk_widget().configure(bg=C["graph_bg"], highlightthickness=0)
        self._cmp_canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=(4, 10))
        self._cmp_canvas.draw()

        # Results table
        tbl_card = ctk.CTkFrame(right, fg_color=C["panel"], corner_radius=10,
                                 border_width=1, border_color=C["border"])
        tbl_card.pack(fill="x")
        ctk.CTkLabel(tbl_card, text="RESULTS TABLE",
                     font=ctk.CTkFont(family="Courier New", size=10, weight="bold"),
                     text_color=C["text_muted"]).pack(anchor="w", padx=14, pady=(10, 4))

        self._cmp_table_frame = ctk.CTkScrollableFrame(
            tbl_card, fg_color="transparent", height=150,
            scrollbar_button_color=C["border"]
        )
        self._cmp_table_frame.pack(fill="x", padx=8, pady=(0, 10))

        self._draw_cmp_table_header()

    def _cmp_refresh_list(self, scroll):
        """Rebuild the full checkbox list and clear previous results."""
        # Destroy all existing checkboxes
        for cb in self._cmp_check_widgets.values():
            cb.destroy()
        self._cmp_checks.clear()
        self._cmp_check_widgets.clear()

        # Rebuild from current server_list (includes any newly added custom servers)
        for srv in self.server_list:
            var = ctk.BooleanVar(value=True)
            cb  = ctk.CTkCheckBox(
                scroll, text=srv, variable=var,
                font=ctk.CTkFont(family="Courier New", size=11),
                text_color=C["text"], fg_color=C["accent"],
                hover_color="#3A8EE6", border_color=C["border"],
                corner_radius=4, height=28
            )
            cb.pack(anchor="w", pady=2)
            self._cmp_checks[srv]        = var
            self._cmp_check_widgets[srv] = cb

        # Clear previous comparison results
        self._cmp_results = {}
        self._cmp_status_lbl.configure(text="List refreshed — run a new comparison")

        # Reset chart to blank
        self._cmp_ax.cla()
        self._cmp_ax.set_facecolor(C["graph_bg"])
        self._cmp_ax.tick_params(colors=C["text_muted"], labelsize=8)
        self._cmp_ax.spines["top"].set_visible(False)
        self._cmp_ax.spines["right"].set_visible(False)
        for sp in ("left", "bottom"):
            self._cmp_ax.spines[sp].set_color(C["border"])
        self._cmp_ax.grid(True, color=C["graph_grid"], linewidth=0.5,
                           linestyle="--", alpha=0.7, axis="y")
        self._cmp_ax.set_ylabel("Latency (ms)", color=C["text_muted"], fontsize=8)
        self._cmp_fig.subplots_adjust(left=0.12, right=0.97, top=0.92, bottom=0.28)
        self._cmp_canvas.draw_idle()

        # Reset results table
        for w in self._cmp_table_frame.winfo_children():
            w.destroy()
        self._draw_cmp_table_header()

    def _run_comparison(self):
        if self._cmp_running:
            return
        selected = [s for s, v in self._cmp_checks.items() if v.get()]
        if not selected:
            messagebox.showwarning("No servers", "Select at least one server.", parent=self._cmp_window)
            return

        self._cmp_running = True
        self._cmp_results = {}
        self._cmp_status_lbl.configure(text=f"Querying {len(selected)} servers…")
        self._cmp_pending = len(selected)

        for srv in selected:
            threading.Thread(target=self._cmp_query_thread, args=(srv,), daemon=True).start()

    def _cmp_query_thread(self, server):
        try:
            _, lat, drift = query_ntp(server, timeout=4.0)
            result = {"latency": lat, "drift": drift, "status": "OK"}
        except Exception:
            result = {"latency": None, "drift": None, "status": "FAIL"}
        self.after(0, self._cmp_collect_result, server, result)

    def _cmp_collect_result(self, server, result):
        self._cmp_results[server] = result
        self._cmp_pending -= 1
        if self._cmp_pending <= 0:
            self._cmp_running = False
            self._cmp_status_lbl.configure(text="Done")
            self._render_comparison()

    def _render_comparison(self):
        results = self._cmp_results
        ok = {s: r for s, r in results.items() if r["status"] == "OK"}
        sorted_ok = sorted(ok.items(), key=lambda x: x[1]["latency"])
        failed    = {s: r for s, r in results.items() if r["status"] == "FAIL"}

        # Bar chart 
        self._cmp_ax.cla()
        self._cmp_ax.set_facecolor(C["graph_bg"])
        self._cmp_ax.tick_params(colors=C["text_muted"], labelsize=8)
        self._cmp_ax.spines["top"].set_visible(False)
        self._cmp_ax.spines["right"].set_visible(False)
        for sp in ("left", "bottom"):
            self._cmp_ax.spines[sp].set_color(C["border"])
        self._cmp_ax.grid(True, color=C["graph_grid"], linewidth=0.5,
                           linestyle="--", alpha=0.7, axis="y")
        self._cmp_ax.set_ylabel("Latency (ms)", color=C["text_muted"], fontsize=8)

        if sorted_ok:
            labels  = [s.replace(".ntp.org","…").replace(".pool.ntp.org","…") for s, _ in sorted_ok]
            values  = [r["latency"] for _, r in sorted_ok]
            colors  = [CMP_COLORS[i % len(CMP_COLORS)] for i in range(len(sorted_ok))]
            bars    = self._cmp_ax.bar(range(len(labels)), values, color=colors,
                                        width=0.55, zorder=3)

            # Label bars with value
            for bar, val in zip(bars, values):
                self._cmp_ax.text(
                    bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                    f"{val:.1f}", ha="center", va="bottom",
                    color=C["text_muted"], fontsize=7, fontfamily="monospace"
                )

            self._cmp_ax.set_xticks(range(len(labels)))
            self._cmp_ax.set_xticklabels(labels, rotation=20, ha="right",
                                           fontsize=7, color=C["text_muted"],
                                           fontfamily="monospace")
            # Crown the winner
            if sorted_ok:
                self._cmp_ax.set_title(
                    f"Fastest: {sorted_ok[0][0]}  ({sorted_ok[0][1]['latency']:.1f} ms)",
                    color=C["accent2"], fontsize=8, fontfamily="monospace",
                    loc="left", pad=4
                )
        else:
            self._cmp_ax.text(0.5, 0.5, "All servers failed",
                               ha="center", va="center",
                               color=C["danger"], fontsize=10,
                               transform=self._cmp_ax.transAxes)

        self._cmp_fig.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.28)
        self._cmp_canvas.draw_idle()

        # Table
        for w in self._cmp_table_frame.winfo_children():
            w.destroy()

        all_rows = sorted_ok + list(failed.items())
        for rank, (srv, res) in enumerate(all_rows):
            row = ctk.CTkFrame(self._cmp_table_frame, fg_color=C["card"] if rank % 2 == 0 else "transparent",
                                corner_radius=4)
            row.pack(fill="x", pady=1)

            # Rank badge for successful
            if res["status"] == "OK":
                badge_text  = f"#{rank+1}"
                badge_color = (C["accent2"] if rank == 0
                               else C["accent"] if rank == 1
                               else C["text_muted"])
            else:
                badge_text  = "FAIL"
                badge_color = C["danger"]

            ctk.CTkLabel(row, text=badge_text,
                          font=ctk.CTkFont(family="Courier New", size=10, weight="bold"),
                          text_color=badge_color, width=38).pack(side="left", padx=(8, 4), pady=4)

            ctk.CTkLabel(row, text=srv,
                          font=ctk.CTkFont(family="Courier New", size=11),
                          text_color=C["text"], anchor="w").pack(side="left", fill="x", expand=True)

            if res["status"] == "OK":
                ql, qc = quality_label(res["latency"])
                ctk.CTkLabel(row, text=f"{res['latency']:.2f} ms",
                              font=ctk.CTkFont(family="Courier New", size=11, weight="bold"),
                              text_color=qc, width=90).pack(side="right", padx=4)
                ctk.CTkLabel(row, text=f"drift {res['drift']:+.2f} ms",
                              font=ctk.CTkFont(family="Courier New", size=10),
                              text_color=C["text_muted"], width=100).pack(side="right", padx=4)
                ctk.CTkLabel(row, text=ql,
                              font=ctk.CTkFont(family="Courier New", size=10, weight="bold"),
                              text_color=qc, width=72).pack(side="right", padx=4)
            else:
                ctk.CTkLabel(row, text="timeout / error",
                              font=ctk.CTkFont(family="Courier New", size=11),
                              text_color=C["danger"]).pack(side="right", padx=12)

    def _draw_cmp_table_header(self):
        hdr = ctk.CTkFrame(self._cmp_table_frame, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 2))
        for text, w, anchor in [
            ("Rank", 38, "center"),
            ("Server", 0, "w"),
            ("Quality", 72, "center"),
            ("Drift", 100, "center"),
            ("Latency", 90, "center"),
        ]:
            ctk.CTkLabel(hdr, text=text, width=w, anchor=anchor,
                          font=ctk.CTkFont(family="Courier New", size=9, weight="bold"),
                          text_color=C["text_muted"]).pack(
                side="left" if anchor == "w" else "right",
                fill="x" if anchor == "w" else None,
                expand=anchor == "w", padx=4
            )

    def _save_comparison_chart(self):
        if not self._cmp_results:
            return
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")],
            initialfile=f"ntp_comparison_{ts}.png",
            title="Save Comparison Chart",
            parent=self._cmp_window
        )
        if path:
            self._cmp_fig.savefig(path, dpi=150, facecolor=C["graph_bg"],
                                   bbox_inches="tight", pad_inches=0.2)

    def _do_query(self):
        if self._querying:
            return
        self._querying = True
        self._query_btn.configure(state="disabled", text="⏳ QUERYING…")
        threading.Thread(target=self._query_thread, daemon=True).start()

    def _query_thread(self):
        server = self.server_var.get()
        try:    ip = socket.gethostbyname(server)
        except: ip = "unresolved"

        try:
            server_dt, latency_ms, drift_ms = query_ntp(server)
            status   = "CONNECTED"
            time_str = server_dt.strftime("%H:%M:%S")
            lat_str  = f"{latency_ms:+.2f} ms"
            drft_str = f"{drift_ms:+.2f} ms"
            self.latency_history.append(latency_ms)
            self.drift_history.append(drift_ms)
            self._all_latencies.append(latency_ms)
            log_csv(server, status, latency_ms, drift_ms)
        except:
            status   = "FAILED"
            time_str = "— error —"
            lat_str  = "— ms"
            drft_str = "— ms"
            latency_ms = drift_ms = None
            self.latency_history.append(0)
            self.drift_history.append(0)
            log_csv(server, status, None, None)
            self.fail_count += 1

        self.poll_count += 1
        self.after(0, self._apply_result,
                   status, time_str, lat_str, drft_str, ip, latency_ms, drift_ms)

    def _apply_result(self, status, time_str, lat_str, drft_str,
                      ip, latency_ms, drift_ms):
        self._querying = False
        self._query_btn.configure(state="normal", text="▶  QUERY")
        self.status_var.set(status)
        self.time_var.set(time_str)
        self.latency_var.set(lat_str)
        self.drift_var.set(drft_str)
        self.ip_var.set(ip)
        self._poll_lbl.configure(text=str(self.poll_count))
        self._fail_lbl.configure(text=str(self.fail_count))

        if self.poll_count > 0:
            loss_pct   = (self.fail_count / self.poll_count) * 100
            loss_color = (C["accent2"] if loss_pct == 0
                          else C["warn"] if loss_pct < 10 else C["danger"])
            self._loss_lbl.configure(text=f"{loss_pct:.1f} %", text_color=loss_color)

        if self._all_latencies:
            mn = min(self._all_latencies)
            mx = max(self._all_latencies)
            av = sum(self._all_latencies) / len(self._all_latencies)
            self.min_var.set(f"{mn:.1f}")
            self.max_var.set(f"{mx:.1f}")
            self.avg_var.set(f"{av:.1f}")

        if latency_ms is not None:
            ql, qc = quality_label(latency_ms)
            self.quality_var.set(ql)
            self._quality_badge.configure(fg_color=qc, text_color=C["bg"])
            for child in self._lat_card.winfo_children():
                if isinstance(child, ctk.CTkLabel) and child.cget("textvariable") == self.latency_var:
                    child.configure(text_color=qc)

        self._status_badge.configure(
            fg_color=C["accent2"] if status == "CONNECTED" else C["danger"],
            text_color=C["bg"]    if status == "CONNECTED" else C["text"]
        )
        self._redraw_graph()
        if self.auto_var.get():
            self._schedule_auto()

    def _redraw_graph(self):
        xs   = list(range(MAX_POINTS))
        lats = list(self.latency_history)
        drfs = list(self.drift_history)

        for coll in self.ax_lat.collections:   coll.remove()
        for coll in self.ax_drift.collections: coll.remove()

        self.lat_line.set_ydata(lats)
        self.lat_dot.set_data([MAX_POINTS-1], [lats[-1]])
        self.ax_lat.fill_between(xs, lats, alpha=0.18, color=C["accent2"])

        self.drft_line.set_ydata(drfs)
        self.drft_dot.set_data([MAX_POINTS-1], [drfs[-1]])
        self.ax_drift.fill_between(xs, drfs, alpha=0.18, color=C["warn"])
        self.ax_drift.axhline(0, color=C["border"], linewidth=0.8, linestyle=":")

        for ax, data in ((self.ax_lat, lats), (self.ax_drift, drfs)):
            lo, hi = min(data), max(data)
            pad = max(abs(hi-lo)*0.25, 2)
            ax.set_ylim(lo-pad, hi+pad)
            ax.set_xlim(0, MAX_POINTS-1)
            ax.set_xticks([])

        self.canvas.draw_idle()

 
    def _schedule_auto(self):
        if self._auto_job: self.after_cancel(self._auto_job)
        self._auto_job = self.after(5000, self._do_query)

    def _toggle_auto(self):
        if self.auto_var.get():
            self._schedule_auto()
        else:
            if self._auto_job:
                self.after_cancel(self._auto_job)
                self._auto_job = None

    def _change_server(self, value):
        self.latency_history = deque([0.0]*MAX_POINTS, maxlen=MAX_POINTS)
        self.drift_history   = deque([0.0]*MAX_POINTS, maxlen=MAX_POINTS)
        self.time_var.set("--:--:--")
        self.latency_var.set("— ms")
        self.drift_var.set("— ms")
        self.quality_var.set("—")
        self._quality_badge.configure(fg_color=C["text_muted"], text_color=C["bg"])
        self._redraw_graph()

    def _save_graph(self):
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
            initialfile=f"ntp_graph_{ts}.png",
            title="Save Graph as PNG"
        )
        if path:
            self.fig.savefig(path, dpi=150, facecolor=C["graph_bg"],
                              bbox_inches="tight", pad_inches=0.2)
            self._flash_status("Graph saved!")

    def _reset_stats(self):
        self._all_latencies.clear()
        self.poll_count = self.fail_count = 0
        self.latency_history = deque([0.0]*MAX_POINTS, maxlen=MAX_POINTS)
        self.drift_history   = deque([0.0]*MAX_POINTS, maxlen=MAX_POINTS)
        for var in (self.min_var, self.max_var, self.avg_var, self.quality_var):
            var.set("—")
        self._quality_badge.configure(fg_color=C["text_muted"], text_color=C["bg"])
        for lbl in (self._poll_lbl, self._fail_lbl):
            lbl.configure(text="0")
        self._loss_lbl.configure(text="0.0 %")
        self._redraw_graph()
        self._flash_status("Stats reset")

    def _open_csv(self):
        if not os.path.exists(LOG_FILE):
            self._flash_status("No log file yet.")
            return
        import subprocess, sys
        if sys.platform == "win32":        os.startfile(LOG_FILE)
        elif sys.platform == "darwin":     subprocess.Popen(["open", LOG_FILE])
        else:                              subprocess.Popen(["xdg-open", LOG_FILE])

    def _flash_status(self, msg):
        old = self.status_var.get()
        self.status_var.set(msg)
        self.after(2000, lambda: self.status_var.set(old))

    def _tick_clock(self):
        self._local_clock.configure(
            text=datetime.datetime.now().strftime("LOCAL  %H:%M:%S"))
        self.after(1000, self._tick_clock)

    def _card(self, parent, title=""):
        frame = ctk.CTkFrame(parent, fg_color=C["panel"], corner_radius=10,
                             border_width=1, border_color=C["border"])
        if title:
            ctk.CTkLabel(
                frame, text=title,
                font=ctk.CTkFont(family="Courier New", size=10, weight="bold"),
                text_color=C["text_muted"]
            ).pack(anchor="w", padx=16, pady=(12, 0))
        return frame

    def _metric_card(self, parent, label, var, icon="", accent=C["accent"]):
        frame = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=10,
                             border_width=1, border_color=C["border"])
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(top, text=f"{icon}  {label}",
                     font=ctk.CTkFont(family="Courier New", size=9, weight="bold"),
                     text_color=C["text_muted"]).pack(anchor="w")
        ctk.CTkLabel(frame, textvariable=var,
                     font=ctk.CTkFont(family="Courier New", size=14, weight="bold"),
                     text_color=accent).pack(anchor="w", padx=14, pady=(2, 10))
        return frame

    def _stat_row(self, parent, label, default, color):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text=label, font=self._label_font(),
                     text_color=C["text_muted"]).pack(side="left")
        lbl = ctk.CTkLabel(row, text=default,
                            font=ctk.CTkFont(family="Courier New", size=12, weight="bold"),
                            text_color=color)
        lbl.pack(side="right")
        return lbl

    def _label_font(self):
        return ctk.CTkFont(family="Courier New", size=11)


# Entry point 
if __name__ == "__main__":
    app = NTPDashboard()
    app.mainloop()