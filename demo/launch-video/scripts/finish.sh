#!/bin/sh
# Two-pass loudness normalisation of the rendered video to -14 LUFS (video copied, audio re-encoded).
# Usage: sh scripts/finish.sh out/launch-raw.mp4 out/jev-harness-launch.mp4
set -eu
in=$1
out=$2
stats=$(ffmpeg -hide_banner -nostats -i "$in" -af loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json -f null - 2>&1 | sed -n '/^{/,/^}/p')
get() { printf '%s' "$stats" | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
ffmpeg -hide_banner -loglevel error -y -i "$in" -c:v copy \
  -af "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=$(get input_i):measured_TP=$(get input_tp):measured_LRA=$(get input_lra):measured_thresh=$(get input_thresh):offset=$(get target_offset):linear=true" \
  -ar 48000 -c:a aac -b:a 320k -movflags +faststart "$out"
echo "input loudness: $(get input_i) LUFS, true peak $(get input_tp) dBTP -> $out"
