# Golf Handicap Tracker

A self-hosted golf handicap calculator built with Flask and SQLite. Tracks rounds, computes your World Handicap System (WHS) index, and provides a printable player report.

Runs entirely on your machine -- no account, no cloud, no subscription.

## Features

### Handicap Calculation

Implements the World Handicap System rules effective January 2024:

- **Sliding scale** -- uses the best 1-8 differentials depending on how many rounds are in your record (3-20)
- **Small sample adjustments** -- applies -2.0 (3 rounds), -1.0 (4 rounds), and -1.0 (6 rounds) per WHS specification
- **0.96 multiplier** on the averaged differentials
- **Soft cap** -- when your index exceeds your Low HI by more than 3.0 strokes, 50% of the excess is removed
- **Hard cap** -- index cannot exceed Low HI + 5.0
- **Low Handicap Index** tracked over a rolling 12-month window
- **Maximum index of 54.0** used as a provisional handicap until 3 rounds are posted
- **Exceptional Score Reduction** -- when a differential is 7.0+ below your index, -1 is applied to all 20 differentials; 10.0+ triggers -2

### Equitable Stroke Control (ESC)

- Net double bogey limit per hole based on course handicap and hole handicap allocation
- Preserves original hole scores while storing adjusted strokes separately
- Displays both actual and adjusted totals when they differ
- If a total score is entered without hole-by-hole detail, it is assumed already stroke-controlled

### 9-Hole Rounds (WHS 2024)

- Record front 9 or back 9 rounds
- Differential calculated using 9-hole course rating and slope
- Expected differential for the unplayed nine (handicap / 2) is added to produce an 18-hole differential
- Optional per-tee front/back 9-hole ratings and slopes; falls back to half the 18-hole rating if not set

### Course & Tee Management

- Add courses with city and country
- Multiple tee sets per course with rating, slope, and par
- Hole-by-hole par and handicap allocation
- Copy tee sets and edit independently
- Optional 9-hole ratings and slopes per tee set

### Round Tracking

- Record rounds with course, tee, date, score, and weather notes
- Hole-by-hole score entry with running total
- ESC indicators on capped holes
- Sort rounds by date or differential
- Rounds used in the current handicap are highlighted

### Scorecard View

- Full scorecard with stroke allocation per hole
- Course handicap and strokes received per hole for a selected player

### Player Report

- Printable report with handicap index, Low HI, and cap info in the header
- Two-column layout: rounds sorted by date and by differential
- Used rounds highlighted, ESC-adjusted scores shown
- Print button with optimized CSS for paper output

### Handicap History

- Automatic snapshots tied to each round
- SVG sparkline chart showing index trend
- History table with change indicators (improved/went up)
- Snapshots update when hole scores are edited and delete when rounds are removed

## Quick Start

### Local (macOS / Linux)

```bash
git clone https://github.com/dankl/GolfHandicap.git
cd GolfHandicap/golf_handicap
pip install -r requirements.txt
python3 app.py
```

Open http://localhost:5000. The database is created automatically at `~/golf_handicap.db`.

To set up a quick-launch alias:

```bash
chmod +x start.sh
echo 'alias golf="~/golf_handicap/start.sh"' >> ~/.zshrc
source ~/.zshrc
```

Then just type `golf` to start. The start scripts kill any previous instance automatically.

### Windows

```
pip install -r requirements.txt
start.bat
```

### Docker

```bash
docker build -t dankl/golf-handicap:latest .

docker run -d \
  -p 5000:5000 \
  -v /path/to/data:/data \
  -e DB_PATH=/data/golf_handicap.db \
  dankl/golf-handicap:latest
```

The image is available on Docker Hub:

```bash
docker pull dankl/golf-handicap:latest
```

### TrueNAS

1. Pull `dankl/golf-handicap:latest` as a custom app container
2. Map port 5000
3. Mount a host path to `/data` for persistent storage
4. Set environment variable `DB_PATH=/data/golf_handicap.db`

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `~/golf_handicap.db` | Path to the SQLite database |
| `SECRET_KEY` | `dev-only-change-in-prod` | Flask session secret key |

## Project Structure

```
golf_handicap/
  app.py              # Flask app, routes, handicap logic, DB schema
  requirements.txt    # Flask, Werkzeug
  Dockerfile          # Python 3.12 slim + gunicorn
  .dockerignore
  start.sh            # macOS/Linux launcher (kills old process first)
  start.bat           # Windows launcher
  static/
    style.css         # All styles including print CSS
  templates/
    base.html         # Base layout with nav and favicon
    index.html        # Player list / home
    dashboard.html    # Player dashboard (rounds, handicap, history)
    courses.html      # Course and tee management
    holes.html        # Hole-by-hole par and handicap editor
    scorecard.html    # Course scorecard with stroke allocation
    report.html       # Printable player report
```

## How It Works

Score differentials are calculated as:

```
Differential = (Adjusted Gross Score - Course Rating) x 113 / Slope Rating
```

For 9-hole rounds, the 9-hole differential is calculated using the nine's rating and slope, then combined with an expected differential (handicap index / 2) for the unplayed nine.

The handicap index is the average of your best differentials (per the sliding scale) multiplied by 0.96, with adjustments for small sample sizes, soft/hard caps against your Low HI, and exceptional score reductions.

ESC limits each hole to net double bogey: par + 2 + any handicap strokes received on that hole. Original scores are preserved; only the adjusted values are used for the differential.

## Data & Portability

All data lives in a single SQLite file. Copy it to any machine running this app to transfer everything. The database path is configurable via the `DB_PATH` environment variable.

## License

MIT
