import subprocess, sys, os
out = subprocess.run([sys.executable, "banner.py"], capture_output=True, text=True, cwd=os.getcwd()).stdout.strip()
want = "LAB-7 :: all systems nominal :: 2026"
if out != want:
    print(f"banner.py printed {out!r} but the lab's status banner is {want!r}")
    sys.exit(1)
print("banner ok")
