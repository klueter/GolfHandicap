# Golf Handicap Tracker

A lightweight local web app for tracking golf scores and calculating your handicap index using the [World Handicap System (WHS)](https://www.usga.org/content/usga/home-page/handicapping/world-handicap-system.html).

Runs entirely on your machine — no account, no cloud, no subscription.

---

## Features

- **Rounds & scoring** — Record rounds across multiple courses and tee sets with hole-by-hole score entry and running totals
- **WHS handicap calculation** — Automatic score differential, sliding-scale round selection, and 0.96 multiplier per the official WHS formula
- **Equitable Stroke Control (ESC)** — Net double bogey limits applied automatically once you have an established handicap (3+ rounds)
- **Automatic handicap snapshots** — Handicap index is recalculated and saved every time you add a round or update hole scores, tagged to the specific round and date
- **Handicap history** — Dedicated card with an SVG sparkline trend chart, color-coded trend arrows, and a tabular history of past snapshots
- **Printable scorecards** — View and print scorecards for any tee set, with handicap strokes pre-calculated for a selected player
- **Course & tee management** — Add courses, define multiple tee sets per course (with rating, slope, and par), and configure hole-by-hole par and handicap rankings
- **Round tracking** — Sort rounds by date or differential, see which rounds count toward your current index
- **Copy tee sets** — Duplicate an existing tee set to quickly set up a new one with similar hole data
- **Single-file database** — All data lives in one portable SQLite file

---

## Requirements

- Python 3.10+
- Flask

---

## Quick Start

### macOS

```bash
git clone https://github.com/yourusername/GolfHandicap.git
cd GolfHandicap/golf_handicap
chmod +x start.sh
./start.sh
```

### Windows

```bash
git clone https://github.com/yourusername/GolfHandicap.git
cd GolfHandicap\golf_handicap
start.bat
```

### Manual

```bash
pip install -r requirements.txt
python3 app.py
```

Then open [http://localhost:5000](http://localhost:5000) in your browser.

The start scripts automatically kill any previously running instance before launching, so you won't get "port already in use" errors.

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-only-change-in-prod` | Flask session secret. Set a strong random value if exposing beyond localhost. |

```bash
SECRET_KEY=your-random-string python3 app.py
```

---

## Project Structure

```
golf_handicap/
├── app.py                # Flask app — routes, handicap logic, DB schema
├── requirements.txt
├── start.sh              # macOS/Linux launcher (kills old process, installs deps, opens browser)
├── start.bat             # Windows launcher
├── static/
│   └── style.css
└── templates/
    ├── base.html         # Shared layout and navigation
    ├── index.html        # Home — golfer selection
    ├── dashboard.html    # Player dashboard — rounds, handicap, snapshots
    ├── courses.html      # Course and tee set management
    ├── holes.html        # Hole-by-hole par and handicap editor
    └── scorecard.html    # Printable scorecard with handicap strokes
```

---

## How the Handicap Calculation Works

The app follows the WHS formula:

1. Takes your most recent 20 rounds
2. Selects the best differentials using the WHS sliding scale (e.g., best 8 of 20, best 1 of 3)
3. Averages the selected differentials and multiplies by 0.96
4. Rounds to one decimal place

Score differentials are calculated as:

```
Differential = (Adjusted Gross Score - Course Rating) x 113 / Slope Rating
```

ESC (net double bogey) is applied per hole when entering hole-by-hole scores, using your course handicap and each hole's handicap ranking.

---

## Data & Portability

The entire database is a single SQLite file at `~/golf_handicap.db`. Copy it to the same location on any other machine running this app to transfer all your data.

---

## License

MIT
