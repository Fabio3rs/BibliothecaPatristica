#!/usr/bin/env python3
# "title": "Patrística Latina, Volume 1", = filename PL001.pdf
# "title": "Patristica Graeca, Volume 1", = filename PG001.pdf
# "title": "Patristica Orientalis. Volume 22.", = filename PO022.pdf

# Extract the title and check if the file already exists
# extract the number from the title
# check missing files

import os

arquivos = []

for filename in os.listdir("."):
    if filename.endswith(".pdf"):
        # Extract the prefix and number from the filename
        parts = filename.split(".")
        if len(parts) == 2:
            prefix = parts[0][:2]  # Get the first two characters as prefix
            number = parts[0][2:]  # Get the rest as number

            # keep only the first digits of the number, if there is a point or dash, take only the digits before it
            number = ''.join(filter(str.isdigit, number.split('.')[0].split('-')[0].split(' ')[0]))
        
            print(f"Prefix: {prefix}, Number: {number}")
            arquivos.append((prefix, number, filename))
        else:
            print(f"Filename {filename} does not match expected format.")
    else:
        print(f"{filename} is not a PDF file.")
        continue

counter = {
    "PL": 0,
    "PG": 0,
    "PO": 0,
    "UN": 0
}

missing_files = []

# unique by prefix and number
arquivos = list(dict.fromkeys(arquivos))
# sort per prefix and number
arquivos.sort(key=lambda x: (x[0], int(x[1])))
print("Sorted files:")
for prefix, number, filename in arquivos:
    print(f"{prefix} {number} {filename}")

# Count the files per prefix
for prefix, number, filename in arquivos:
    if number == "":
        print(f"File {filename} has no number, skipping.")
        continue

    if prefix in counter:
        counter[prefix] += 1
    else:
        counter[prefix] = 1

    number = int(number) if number.isdigit() else 0

    current_count = counter.get(prefix, 0)
    if number > current_count:
        if os.path.exists(f"{prefix}{number:03d}.pdf"):
            print(f"File {prefix}{number:03d}.pdf already exists, skipping.")
        else:
            missing_files.append((prefix, current_count + 1))

    current_count = number

print("Done checking for missing files.")
for prefix, number in missing_files:
    print(f"Missing file: {prefix}{number:03d}.pdf")
    if prefix == "PL":
        print(f"Missing file: {prefix}{number:03d}.pdf - Patrística Latina")
    elif prefix == "PG":
        print(f"Missing file: {prefix}{number:03d}.pdf - Patrística Greca")
    elif prefix == "PO":
        print(f"Missing file: {prefix}{number:03d}.pdf - Patrística Orientalis")
    else:
        print(f"Missing file: {prefix}{number:03d}.pdf - Unknown prefix")

print(f"Total unique files found: {len(arquivos)}")
import json
print(f"Total missing files: {len(missing_files)}")
print(f"Counter: {counter}")

arquivos_json = []
with open('drivegoogle2.json', 'r') as file:
        arquivos_json = json.load(file)

# Count the number of files in the JSON
arquivos_json = [item for sublist in arquivos_json for item in sublist]
print(f"Total files in JSON: {len(arquivos_json)}")
for item in arquivos_json:
    title = item.get("title", "Unknown Title")
    link = item.get("link", "")
    
    # Extract the prefix and number from the title
    title_lower = title.lower().strip()
    if "latina" in title_lower:
        prefix = "PL"
    elif "greca" in title_lower or "graeca" in title_lower or "græca" in title_lower:
        prefix = "PG"
    elif "orientalis" in title_lower:
        prefix = "PO"
    else:
        prefix = "UN"

    number = ''.join(filter(str.isdigit, title_lower))
    
    if not number:
        print(f"Title {title} has no number, skipping.")
        continue

    if (prefix, number) not in [(p, n) for p, n, f in arquivos]:
        if not os.path.exists(f"{prefix}{number.zfill(3)}.pdf"):
            print(f"Missing file from JSON: {prefix}{number.zfill(3)}.pdf - {title}")
