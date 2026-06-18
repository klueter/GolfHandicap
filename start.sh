#!/bin/bash
cd /Users/dankl/golf_handicap
pip3 install -r requirements.txt -q
python3 app.py &
sleep 2
open -a Firefox http://localhost:5001
