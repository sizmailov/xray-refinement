#!/bin/bash


source venv/bin/activate

export AMBERHOME="/opt/amber/"
export AMBER_PREFIX="$AMBERHOME"
export PATH="$AMBERHOME/bin:$PATH"

cd test
python ../init.py