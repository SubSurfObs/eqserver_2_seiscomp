# PC-SUDS data from Echos and EchoPros

## overview

* Records minute long PC-SUDS files, with extension `.dmx`
* Recorder has an option to compress, which gives`.dmx.gz`
* Telemetered files have spaces: `2024 2024-01-01 2359 02 ABM5Y.dmx`
* Locally saved files have underscores: `2024 2024-01-01_2359_02_ABM5Y.dmx`


## SUDS Data in EqServer

In the EqServer Continuous Archive you may findL
* both telemetered and locally saved files
* multiple underscored (or spaced - probably) files for a given HHMM
* sometimes you also get triggered files, which look like `...trig.dmx.gz`


e.g:

```
2023-10-23_2358_19_ABM2Y.dmx
2023-10-23_2358_31_ABM2Y.dmx
``

## Use of EqConvert on SUDS datya


## Copying data for conversion

The following loop tries to copy across a file for each HHMM combination, taking the underscored file by preferebce (when both types of file are present). 

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
