#!/bin/bash

tmux new-session -d -s main -x 150 -y 40 \; send-keys 'cd ~/vg101/notebooks/wiki' C-m \; split-window -v -p 60 -t 0 \; send-keys 'cd ~/vg101' C-m \; select-pane -t 0 \; split-window -h -p 50 -t 0 \; send-keys 'cd ~' C-m \; send-keys 'htop' C-m \; select-pane -t 0 \; attach-session -t main \; new-window\; send-keys 'cd ~' C-m\; split-wind -v -p 50 -t 0 \; send-keys 'cd ~' C-m \; select-pane -t 0 \; select-window -t 0
