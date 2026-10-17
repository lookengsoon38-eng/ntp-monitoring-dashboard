# ntp-monitoring-dashboard
A Python desktop dashboard for monitoring NTP servers, analyzing network latency and clock drift, and visualizing time synchronization performance.

# Client Network Time Protocol (NTP) Monitoring Dashboard

## Overview

A Python desktop application developed to monitor and analyze Network Time Protocol (NTP) server performance. The dashboard aims to simplify time synchronization monitoring through a graphical interface and performance visualization.

## Features

- **Real-Time Monitoring:** Measures NTP server response latency and estimates clock drift.
- **Interactive Dashboard:** Displays monitoring statistics and performance trends.
- **Server Comparison:** Compares multiple NTP servers to evaluate their responsiveness and stability.
- **Performance Visualization:** Uses graphs to present latency and clock drift measurements.
- **CSV Logging:** Records monitoring results for further analysis.
- **Multithreading:** Keeps network operations separate from GUI processing to maintain interface responsiveness.

## Technologies Used

- Python
- CustomTkinter
- Matplotlib
- UDP socket programming
- CSV data handling
- Multithreading

## Installation and Usage

1. Clone the repository:

```bash
git clone https://github.com/lookengsoon38-eng/ntp-monitoring-dashboard.git
cd ntp-monitoring-dashboard
```

2. Install the required libraries:

```bash
pip install customtkinter matplotlib
```

3. Run the application:

```bash
python ntp_dashboard.py
```


