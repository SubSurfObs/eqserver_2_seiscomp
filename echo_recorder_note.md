# PC-SUDS data from Echos and EchoPros

## overview

* Records minute long PC-SUDS files, with extension `.dmx`
* Recorder has an option to compress, which gives`.dmx.gz`
* Telemetered files have spaces: `2024 2024-01-01 2359 02 ABM5Y.dmx`
* Locally saved files have underscores: `2024 2024-01-01_2359_02_ABM5Y.dmx`


## SUDS Data in EqServer

In the EqServer Continuous Archive you may findL
* both telemetered and locally saved files
* multiple underscored (or spaced files - probably) files for a given HHMM seems relatively commo
  * this means you  never want to assume that 24x60 files should be present. 
* sometimes you also get triggered files in the continuous , these files look like `...trig.dmx.gz`


e.g:

```
2023-10-23_2358_19_ABM2Y.dmx
2023-10-23_2358_31_ABM2Y.dmx
```

## Use of EqConvert on SUDS datya


## Copying data for conversion

The script loops over every hour and minute of the day, constructing an HHMM string for each, and for each HHMM it copies all matching underscored .dmx or .dmx.gz files from the input directory to the output directory, skipping any .trig files; if no underscored files exist for that HHMM, it copies all matching spaced files instead, ensuring every available minute file is captured while preferring underscored files and handling multiple files per minute.

```
#!/bin/bash
# Usage: ./copy_minute_files.sh input_dir output_dir

in_dir="$1"
out_dir="$2"

if [ -z "$in_dir" ] || [ -z "$out_dir" ]; then
    echo "Usage: $0 input_dir output_dir"
    exit 1
fi

# Ensure absolute path for output
out_dir=$(realpath "$out_dir")
mkdir -p "$out_dir"

# Loop over hours and minutes
for hh in $(seq -w 0 23); do
    for mm in $(seq -w 0 59); do
        hhmm="${hh}${mm}"

        # Prefer underscored file
        file=$(ls "$in_dir"/*_"$hhmm"_* 2>/dev/null | grep -v '\.trig' | head -n1)

        # If no underscored, try spaced file
        if [ -z "$file" ]; then
            file=$(ls "$in_dir"/*\ "$hhmm"\ * 2>/dev/null | grep -v '\.trig' | head -n1)
        fi

        # Copy if a file exists
        if [ -n "$file" ]; then
            cp "$file" "$out_dir/"
            echo "Copied: $file"
        fi
    done
done
```
