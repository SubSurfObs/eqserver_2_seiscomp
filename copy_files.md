# Copyign files

A challenge with the eqserver archive is that it often consists of duplicate files, telemetered and locally stored fiels that have been uploaded later. 

These can be differentiated via the use of underscores. I've developed a script which can deal with both miniseed files from the Geckos and PC suds from the Echo Pros. 

The basic idea is to copy across a unique set of files, taking the underscores by preference. That logic could be changed potentially to take the larger file by preference. However, in all cases, we save three-component data as underscores, whereas we only sometimes telemate a single component. So this script simply copies those to a temporary directory. 

This seems to be quite slow, and it's something to look at alternative approaches to. I don't want to work with the data in place because that will mess up the eqserver archive.


The following script copies over file on order to take either the underscored file (firts preference), or the spaced file. It's been tested on a directory with a mix of file types (spaces and underscores)
This is slow, however - need to work on ways to make this faster, or have it work in place - i.e. build up a file list. 

```
#!/bin/bash

# Default verbose off
VERBOSE=0

# Check for verbose flag
if [[ "$1" == "-v" || "$1" == "--verbose" ]]; then
    VERBOSE=1
    shift
fi

in_dir="$1"
out_dir="$2"

mkdir -p "$out_dir"

# Detect file type automatically
shopt -s nullglob
files=("$in_dir"/*.{ms*,dmx,dmx.gz})
if [ ${#files[@]} -eq 0 ]; then
    echo "No matching files found in $in_dir"
    exit 1
fi

# Keep track of which minutes already have an underscored file
declare -A underscored_exists

for file in "${files[@]}"; do
    [ -e "$file" ] || continue
    base=$(basename "$file")

    # Extract HHMM from either style (works for both ms and dmx)
    if [[ "$base" =~ ([0-2][0-9][0-5][0-9]) ]]; then
        hhmm="${BASH_REMATCH[1]}"
    else
        [[ $VERBOSE -eq 1 ]] && echo "Cannot extract HHMM from $base"
        continue
    fi

    if [[ "$base" == *_*_* ]]; then
        # Underscored file: copy and mark HHMM
        cp "$file" "$out_dir/"
        underscored_exists["$hhmm"]=1
        [[ $VERBOSE -eq 1 ]] && echo "Copied underscored: $file"
    else
        # Spaced file: only copy if no underscored for this HHMM
        if [[ -z "${underscored_exists[$hhmm]}" ]]; then
            cp "$file" "$out_dir/"
            [[ $VERBOSE -eq 1 ]] && echo "Copied spaced: $file"
        else
            [[ $VERBOSE -eq 1 ]] && echo "Skipped spaced (duplicate minute): $file"
        fi
    fi
done

```






