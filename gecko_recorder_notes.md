## Miniseed data from Geckos

## Zipped files

Here is an example where we have a telemetered and non-telemetered file:

```
'/data/repository/archive/ABM1Y/continuous/2023/10/29/2023-10-29 0001 00 ABM1Y.ms.zip'
/data/repository/archive/ABM1Y/continuous/2023/10/29/20231029_0001_ABM1Y.ms.zip
```

```
unzip '/data/repository/archive/ABM1Y/continuous/2023/10/29/2023-10-29 0001 00 ABM1Y.ms.zip' -d test_unzip_formats
Archive:  /data/repository/archive/ABM1Y/continuous/2023/10/29/2023-10-29 0001 00 ABM1Y.ms.zip
  inflating: test_unzip_formats/2023-10-29 0001 00 ABM1Y.ms  
```

```
unzip '/data/repository/archive/ABM1Y/continuous/2023/10/29/20231029_0001_ABM1Y.ms.zip' -d test_unzip_formats
Archive:  /data/repository/archive/ABM1Y/continuous/2023/10/29/20231029_0001_ABM1Y.ms.zip
  inflating: test_unzip_formats/20231029_0001_ABM1Y.ms  
  inflating: test_unzip_formats/ABM1Y.02000236.ss  
  inflating: test_unzip_formats/ABM1Y.02000236.station.xml
```

So, the underscored zip file (USB/Local file) contains multiple files

## Copying files

The following script copies over file on order to take either the underscored file (firts preference(, or the spaced file. It's been tested on a directre with a mix of file types

```                                                                                                    
#!/bin/bash

in_dir="$1"
out_dir="$2"

mkdir -p "$out_dir"

# Keep track of which minutes already have an underscored file
declare -A underscored_exists

for file in "$in_dir"/*.ms*; do
    [ -e "$file" ] || continue
    base=$(basename "$file")

    # Extract HHMM from underscored (e.g., 20231029_0001_ABM1Y.ms) or spaced (2023-10-29 0001 00 ABM1Y.ms)
    if [[ "$base" =~ ([0-2][0-9][0-5][0-9]) ]]; then
        hhmm="${BASH_REMATCH[1]}"
    else
        echo "Cannot extract HHMM from $base"
        continue
    fi

    if [[ "$base" == *_*_* ]]; then
        # Underscored file: copy and mark HHMM
        cp "$file" "$out_dir/"
        underscored_exists["$hhmm"]=1
        echo "Copied underscored: $file"
    else
        # Spaced file: only copy if no underscored for this HHMM
        if [[ -z "${underscored_exists[$hhmm]}" ]]; then
            cp "$file" "$out_dir/"
            echo "Copied spaced: $file"
        fi
    fi
done
```
