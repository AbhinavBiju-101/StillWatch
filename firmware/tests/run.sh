#!/bin/sh
# Compile and run the host-side firmware tests (needs only g++). From the repo root:  sh firmware/tests/run.sh
set -e
cd "$(dirname "$0")"
g++ -std=c++17 -Wall -Wextra -Wno-unused-parameter -fsanitize=address,undefined -I../water_node test_logic.cpp -o /tmp/stillwatch_test_logic
/tmp/stillwatch_test_logic
