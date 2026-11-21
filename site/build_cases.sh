#!/bin/sh
# Rebuild site/media/cases/ from the case studies' own files (acceptance/cases/*/files).
# Pictures are cropped and resized for the web with metadata dropped; the two clips are
# copied as they are. Credits for every file are in each case's files/ATTRIBUTION.md.
set -eu
cd "$(dirname "$0")/.."
src=acceptance/cases
out=site/media/cases
mkdir -p "$out"

pic() { # source, crop (w:h:x:y or "none"), width, output
  vf="scale=$3:-2"
  [ "$2" = none ] || vf="crop=$2,$vf"
  ffmpeg -v error -y -i "$1" -vf "$vf" -map_metadata -1 -q:v 4 "$out/$4"
}

f=$src/four-names/files
pic "$f/ship_agent_licence.jpg" 1052:789:80:90 900 four-names-licence.jpg
pic "$f/vendor_form_kassar.jpg" 1120:1000:60:60 560 four-names-vendor.jpg
pic "$f/consultancy_agreement_qassar.jpg" 1120:1000:60:60 560 four-names-agreement.jpg
pic "$f/guarantee_deed_khassar.jpg" 1120:1000:60:60 560 four-names-guarantee.jpg
pic "$f/ship_agent_licence.jpg" 1120:1000:60:60 560 four-names-agent.jpg

f=$src/never-there/files
pic "$f/IMG_4471.jpg" none 1000 never-there-gate.jpg
cp "$f/voice_note.ogg" "$out/never-there-voice-note.ogg"

f=$src/old-footage/files
cp "$f/original.mp4" "$out/old-footage-original.mp4"
cp "$f/copy_viral.mp4" "$out/old-footage-viral.mp4"
ffmpeg -v error -y -ss 7 -i "$f/original.mp4" -frames:v 1 -q:v 4 "$out/old-footage-original.jpg"
ffmpeg -v error -y -ss 4 -i "$f/copy_viral.mp4" -frames:v 1 -q:v 4 "$out/old-footage-viral.jpg"

f=$src/unwritten-name/files
pic "$f/gate_book_14_march.jpg" 1120:840:60:60 900 unwritten-name-gate-book.jpg
pic "$f/supervisor_statement.jpg" 1120:1000:60:40 560 unwritten-name-statement.jpg
pic "$f/wenmere_depot.jpg" none 1200 unwritten-name-depot.jpg
