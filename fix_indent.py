import sys

with open("server.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i in range(147, 329): # lines 148 to 329
    lines[i] = "    " + lines[i]

with open("server.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Fixed indentation!")
