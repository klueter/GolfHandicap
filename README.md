# Golf Handicap Tracker

A lightweight local web app for tracking golf scores and calculating your handicap index using the [World Handicap System (WHS)](https://www.usga.org/content/usga/home-page/handicapping/world-handicap-system.html).

Runs entirely on your machine — no account, no cloud, no subscription.

---

## Features

- Track rounds across multiple courses and tee sets
- Automatic score differential calculation
- WHS-compliant handicap index (sliding scale, 0.96 multiplier)
- Equitable Stroke Control (ESC) applied to hole scores
- Hole-by-hole score entry with running total
- Identifies which rounds count toward your current index
- Sort rounds by date or differential
- Handicap snapshot history

---

## Requirements

- Python 3.10+
- Flask

---

## Installation

```bash
git clone https://github.com/your-username/golf-handicap-tracker.git
cd golf-handicap-tracker
pip install flask
```

---

## Running

```bash
python app.py
```

Then open [http://localhost:5001](http://localhost:5001) in your browser.

The database (`golf_handicap.db`) is created automatically in your home directory on first run.

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-only-change-in-prod` | Flask session secret. Set a strong random value if exposing beyond localhost. |

```bash
# Example
SECRET_KEY=your-random-string python app.py
```

---

## Project structure

```
golf_handicap/
├── app.py              # Flask app and all routes
├── requirements.txt
├── static/
│   └── style.css
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── index.html
    ├── courses.html
    └── holes.html
```

---

## Transferring data

The entire database is a single SQLite file at `~/golf_handicap.db`. Copy it to the same location on any other machine running this app.

---

## License

MIT
