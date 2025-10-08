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
* Locally saved files have underscores: `2024 2024-01-01_2359_02_ABM5Y.dmx
* sometimes you also get triggered files in the continuous , these files look like `...trig.dmx.gz`
* sometimes you get a few errant mseed files (??) that have single underscore, like '2023-10-24 0836 49 MARD_DHZ.mseed.zip' (this is one of about 50 such files in a day that otherwise has only telemetered data)
* sometimes you get accelerometer files that are triggered and do not have trig in the name e.g., `2023-11-24_0317_55_ABM5Y.dmx`. this is harder to deal with.

## filtering correct files:

sometimes you get accelerometer files that are triggered and do not have trig in the name e.g., `2023-11-24_0317_55_ABM5Y.dmx`. this is harder to deal with. Howverm because these are triggered , they do not have a common time stamp (as in seconds). 

```
seiscomp@rd-l-y9d9pt:~/sds_conversion_tests$ ls /data/repository/archive/ABM5Y/continuous/2023/11/24  | awk -F'_' 'NF==4' | wc -l
1480
```

One way of dealing with this is to check for files that have a uniform timestamp. My hope is that this cover something like 80% of cases, ie recorder functioning and 1440 files present. So this is the first check to perform - look for a full (or near full) complement of files. 

scripts/count_underscored.sh /data/repository/archive/ABM5Y/continuous/2023/11/24

```
seiscomp@rd-l-y9d9pt:~/sds_conversion_tests$ scripts/count_underscored.sh /data/repository/archive/ABM5Y/continuous/2023/11/24
Directory: /data/repository/archive/ABM5Y/continuous/2023/11/24
Most common timestamp: 02
Number of files with this timestamp and 3 underscores: 1440
```

If this returns less that 1440, there are a few options. We could copy over the files that have a unique pattern and are abouve a threshold number of files. This should get the continuous files even when the recorder was switching on and off. 

So the logic might be. 
* Check total undercored and spaced files, excluding known files like "trig", "mseed". etc/
* if 1440 spaced files copy over.
* if more than 1440 get unique time stamps and frequence. copy over those above a threshold.
* if less that 1440 repeat process for spaced files

The limitation will be if triggered accelerometer files with the same pattern overwrite the seismometer files, and coincidently have the same file time stamp.  


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
