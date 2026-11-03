import subprocess, sys, os
out = subprocess.run([sys.executable, "greet.py"], capture_output=True, text=True, cwd=os.getcwd()).stdout.strip()
if out != "Hello, Research OS!":
    print(f"greet.py printed {out!r} but the lab's standard greeting line is 'Hello, Research OS!'")
    sys.exit(1)
print("greeting ok")
