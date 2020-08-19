#!/bin/bash

pkill -2 -f "python.*sparrowwifiagent.py.*"
sleep 1
pkill -9 -f "python.*sparrowwifiagent.py.*"
