#!/usr/bin/env bash
# OM-005: record WSL VA-API availability (expect unavailable without GPU passthrough).
set -u
TMP=/tmp/om_d4
mkdir -p "$TMP"
REPORT="$TMP/wsl-report.txt"
{
  echo "uname=$(uname -a)"
  echo "ffmpeg=$(ffmpeg -version | head -n 1)"
  if command -v vainfo >/dev/null 2>&1; then echo "vainfo=present"; else echo "vainfo=absent"; fi
  if [ -d /dev/dri ]; then ls -la /dev/dri; else echo "dri=absent"; fi
  ffmpeg -y -hide_banner -loglevel error -f lavfi -i testsrc=size=640x360:rate=30 -frames:v 30 -pix_fmt yuv420p "$TMP/src.yuv"
  for enc in libx264 h264_vaapi; do
    set +e
    ffmpeg -y -hide_banner -loglevel error -f rawvideo -pix_fmt yuv420p -s 640x360 -r 30 -i "$TMP/src.yuv" -c:v "$enc" -b:v 2M -frames:v 30 "$TMP/$enc.mp4" >"$TMP/$enc.log" 2>&1
    ec=$?
    set -e
    sz=0
    if [ -f "$TMP/$enc.mp4" ]; then sz=$(stat -c%s "$TMP/$enc.mp4"); fi
    echo "enc=$enc exit=$ec bytes=$sz"
    echo -n "stderr="
    tr "\n" " " < "$TMP/$enc.log" | tail -c 300
    echo
  done
} > "$REPORT"
cat "$REPORT"
