# PC-SUDS data from Echos and EchoPros

## overview

* Records minute long PC-SUDS files, with extension `.dmx`
* Recorder has an option to compress, which gives`.dmx.gz`
* Telemetered files have spaces: `2024 2024-01-01 2359 02 ABM5Y.dmx`
* Locally saved files have underscores: `2024 2024-01-01_2359_02_ABM5Y.dmx`


## Unzipping

e.g., 

```
#-d decompress, k keep oroginal file
gzip -dk 2024-01-01\ 0000\ 52\ FRTM.dmx.gz
```

This results in a single PC-SUDS file, eg *.FRTM.dmx

## SUDS Data in EqServer

In the EqServer Continuous Archive you may find:

* both telemetered and locally saved files
* Telemetered files have spaces: `2024 2024-01-01 2359 02 ABM5Y.dmx`
* Locally saved files have underscores: `2024 2024-01-01_2359_02_ABM5Y.dmx`

* sometimes you also get triggered files in the continuous , these files look like `...trig.dmx.gz`
* sometimes you get accelerometer files that are triggered and do not have trig in the name e.g., `2023-11-24_0317_55_ABM5Y.dmx`. this is harder to deal with. 

## Use of EqConvert on SUDS data


## Copying data for conversion

The script loops over every hour and minute of the day, constructing an HHMM string for each, and for each HHMM it copies all matching underscored .dmx or .dmx.gz files from the input directory to the output directory, skipping any .trig files; if no underscored files exist for that HHMM, it copies all matching spaced files instead, ensuring every available minute file is captured while preferring underscored files and handling multiple files per minute.

This has been through several iterations, as it broke on different cases. Best to keep it as a standalone script.

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

        # Check if any underscored files exist for this HHMM
        if find "$in_dir" -maxdepth 1 -type f -name "*_${hhmm}_*" ! -name "*.trig*" -print -quit | grep -q .; then
            # Copy all underscored files safely
            find "$in_dir" -maxdepth 1 -type f -name "*_${hhmm}_*" ! -name "*.trig*" -print0 2>/dev/null | \
            while IFS= read -r -d '' file; do
                cp "$file" "$out_dir/"
                echo "Copied underscored: $file"
            done
        else
            # Copy all spaced files safely if no underscored files exist
            find "$in_dir" -maxdepth 1 -type f -name "* $hhmm *" ! -name "*.trig*" -print0 2>/dev/null | \
            while IFS= read -r -d '' file; do
                cp "$file" "$out_dir/"
                echo "Copied spaced: $file"
            done
        fi

    done
done
```
