#!/bin/bash
# Switch synthetic-images.sh back on: removes the flag file CRON_OFF that off.sh wrote.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
# A launcher renamed by the old off.sh (<name>-off.sh, before 2026-10-25) is put back,
# or dropped when git has already restored the tracked one.
[ -f synthetic-images-off.sh ] && { [ -f synthetic-images.sh ] && rm -f synthetic-images-off.sh || mv synthetic-images-off.sh synthetic-images.sh; }
rm -f CRON_OFF && echo "synthetic-images: on (CRON_OFF removed)"
