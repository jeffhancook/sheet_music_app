"""Test pyin pitch tracking vs basic-pitch for monophonic transcription."""
import sys
import time
import numpy as np
import librosa
import pretty_midi

audio_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/test_melody.wav"

print(f"Loading: {audio_path}")
y, sr = librosa.load(audio_path, sr=22050, mono=True)
duration = len(y) / sr
print(f"Duration: {duration:.1f}s, sr={sr}")

# ── pyin pitch tracking ──
t0 = time.time()
# fmin=G3 (196Hz), fmax=E7 (2637Hz) — violin range
f0, voiced_flag, voiced_prob = librosa.pyin(
    y, fmin=196, fmax=2637, sr=sr,
    frame_length=2048, hop_length=512,
)
pyin_time = time.time() - t0

# Convert frames to time
times = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=512)

print(f"\npyin completed in {pyin_time:.2f}s")
print(f"Total frames: {len(f0)}")
voiced_count = np.sum(voiced_flag)
print(f"Voiced frames: {voiced_count} ({100*voiced_count/len(f0):.1f}%)")

# Show some detected pitches
print("\nFirst 50 voiced pitches:")
for i in range(min(len(f0), 200)):
    if voiced_flag[i] and f0[i] is not None and not np.isnan(f0[i]):
        midi_note = librosa.hz_to_midi(f0[i])
        note_name = librosa.hz_to_note(f0[i])
        print(f"  t={times[i]:.3f}s  f={f0[i]:.1f}Hz  midi={midi_note:.1f}  note={note_name}")

# ── Convert to MIDI ──
# Group consecutive voiced frames with similar pitch into notes
MIN_NOTE_FRAMES = 3  # minimum ~70ms to be a real note

notes = []
current_start = None
current_pitches = []

for i in range(len(f0)):
    if voiced_flag[i] and f0[i] is not None and not np.isnan(f0[i]):
        midi = librosa.hz_to_midi(f0[i])
        if current_start is None:
            current_start = i
            current_pitches = [midi]
        else:
            # Check if pitch jumped (new note) — more than 1 semitone
            if abs(midi - np.median(current_pitches)) > 1.0:
                # Save previous note
                if len(current_pitches) >= MIN_NOTE_FRAMES:
                    notes.append((current_start, i, np.median(current_pitches)))
                current_start = i
                current_pitches = [midi]
            else:
                current_pitches.append(midi)
    else:
        # Unvoiced — end current note
        if current_start is not None and len(current_pitches) >= MIN_NOTE_FRAMES:
            notes.append((current_start, i, np.median(current_pitches)))
        current_start = None
        current_pitches = []

# Don't forget last note
if current_start is not None and len(current_pitches) >= MIN_NOTE_FRAMES:
    notes.append((current_start, len(f0), np.median(current_pitches)))

print(f"\nExtracted {len(notes)} notes")
print("\nFirst 30 notes:")
for start_frame, end_frame, midi_pitch in notes[:30]:
    t_start = times[start_frame]
    t_end = times[min(end_frame, len(times) - 1)]
    dur = t_end - t_start
    rounded_midi = int(round(midi_pitch))
    note_name = librosa.midi_to_note(rounded_midi)
    print(f"  {t_start:6.2f}s - {t_end:6.2f}s  ({dur:.2f}s)  midi={rounded_midi:3d} ({note_name})")

# Write MIDI
pm = pretty_midi.PrettyMIDI(initial_tempo=120)
violin = pretty_midi.Instrument(program=40, name="Violin")
for start_frame, end_frame, midi_pitch in notes:
    t_start = times[start_frame]
    t_end = times[min(end_frame, len(times) - 1)]
    rounded_midi = int(round(midi_pitch))
    # Clamp to violin range
    while rounded_midi < 55:
        rounded_midi += 12
    while rounded_midi > 100:
        rounded_midi -= 12
    n = pretty_midi.Note(velocity=80, pitch=rounded_midi, start=t_start, end=t_end)
    violin.notes.append(n)
pm.instruments.append(violin)
pm.write("/tmp/pyin_output.mid")
print(f"\nWrote /tmp/pyin_output.mid ({len(notes)} notes)")
