#!/bin/bash
cd /Users/dankl/golf_handicap

# Kill any existing app.py process
pkill -f "python3 app.py" 2>/dev/null
sleep 1

pip3 install -r requirements.txt -q
python3 app.py &
sleep 2
open -a Firefox http://localhost:5000
