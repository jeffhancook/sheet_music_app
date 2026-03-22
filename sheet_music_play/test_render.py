"""Test script: post-process music21 lilypond output and render PDF."""
import re
import subprocess
import os

with open("/tmp/test_clean.ly") as f:
    ly = f.read()

# 1. Remove lilypond-book-preamble (causes tiny page sizing)
ly = ly.replace('\\include "lilypond-book-preamble.ly"', '')

# 2. Remove color function definition (unused)
ly = re.sub(r"color\s*=\s*#\(define-music-function.*?#\}\)", "", ly, flags=re.DOTALL)

# 3. Remove \autoBeamOff (let LilyPond beam naturally)
ly = ly.replace("\\autoBeamOff", "")

# 4. Remove stem direction overrides (let LilyPond decide)
ly = re.sub(r"\\set stem(?:Right|Left)BeamCount = #\d+\s*\n", "", ly)
ly = re.sub(r"\\once \\override Stem\.direction = #(?:UP|DOWN)\s*\n", "", ly)

# 5. Remove manual beam brackets since autoBeam is back on
ly = re.sub(r"\s*\[\s*", " ", ly)
ly = re.sub(r"\s*\]\s*", " ", ly)

# 6. Add paper block after \header { ... }
paper_block = (
    "\n\\paper {\n"
    '  #(set-paper-size "letter")\n'
    "  top-margin = 12\\mm\n"
    "  bottom-margin = 12\\mm\n"
    "  left-margin = 12\\mm\n"
    "  right-margin = 12\\mm\n"
    "}\n"
)
ly = ly.replace("\\score", paper_block + "\n\\score", 1)

# 7. Clean up excessive blank lines
ly = re.sub(r"\n{3,}", "\n\n", ly)

with open("/tmp/test_final.ly", "w") as f:
    f.write(ly)

# Render
result = subprocess.run(
    ["lilypond", "--pdf", "-o", "/tmp/test_final", "/tmp/test_final.ly"],
    capture_output=True, text=True, timeout=120,
)
print(f"Exit code: {result.returncode}")
if result.returncode != 0:
    for line in result.stderr.splitlines():
        if "error" in line.lower():
            print(line)
    print("Last 10 stderr lines:")
    print("\n".join(result.stderr.splitlines()[-10:]))
else:
    sz = os.path.getsize("/tmp/test_final.pdf")
    print(f"PDF: {sz} bytes")

# Show first 40 lines
print("\n--- First 40 lines ---")
with open("/tmp/test_final.ly") as f:
    for i, line in enumerate(f.readlines()[:40]):
        print(f"{i+1:3}: {line}", end="")
