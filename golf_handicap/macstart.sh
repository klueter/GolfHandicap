#!/bin/bash
if [ "$1" = "stop" ]; then
    pkill -f "python3 app.py" 2>/dev/null
    echo "Golf Handicap Tracker stopped."
    exit 0
fi

cd /Users/dankl/Documents/GitHub/GolfHandicap/golf_handicap

# Kill any existing app.py process
pkill -f "python3 app.py" 2>/dev/null
sleep 1

pip3 install -r requirements.txt -q
python3 app.py &
sleep 2
open -a Firefox http://localhost:5000
